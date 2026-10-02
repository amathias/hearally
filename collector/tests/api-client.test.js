import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, DEVICE_TOKEN, HearallyClient, redeemPairing } from "../src/api-client.js";

const TOKEN = `hd1.3f1c2a90-1111-4222-8333-944455556666.${"a".repeat(32)}.${"s".repeat(43)}`;

function fakeFetch(responses) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const next = responses.shift() ?? { status: 200, body: {} };
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      json: async () => next.body,
    };
  };
  return { calls, fetchImpl };
}

function client(fetchImpl) {
  return new HearallyClient({
    apiUrl: "https://api.hearally.com/",
    deviceToken: TOKEN,
    fetchImpl,
  });
}

test("every API call carries this computer's device token, and nothing else identifies it", async () => {
  const { calls, fetchImpl } = fakeFetch([{ status: 202, body: { accepted: true } }]);
  await client(fetchImpl).ingest({ schema_version: 4, event_id: "abc" });

  const { url, init } = calls[0];
  assert.equal(url, "https://api.hearally.com/v1/ingest");
  assert.equal(init.headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(init.redirect, "error", "a redirect must not carry the token elsewhere");
  assert.ok(!init.body.includes("account"), "the account is the server's to decide");
});

test("the audio upload to S3 never carries the device token", async () => {
  const { calls, fetchImpl } = fakeFetch([{ status: 204, body: {} }]);
  await client(fetchImpl).putAudio({ upload_url: "https://s3/", upload_fields: {} }, Buffer.from("a"));
  assert.equal(calls[0].init.headers, undefined);
});

test("a pairing code is traded for a token, sent in the body and never the URL", async () => {
  const code = "C".repeat(32);
  const { calls, fetchImpl } = fakeFetch([{ status: 201, body: { token: TOKEN, device_id: "a".repeat(32) } }]);
  const token = await redeemPairing({ apiUrl: "https://api.hearally.com", code, computerName: "Laptop", fetchImpl });
  assert.equal(token, TOKEN);
  assert.equal(calls[0].url, "https://api.hearally.com/v1/pairings/redeem");
  assert.ok(!calls[0].url.includes(code));
  assert.deepEqual(JSON.parse(calls[0].init.body), { code, computer_name: "Laptop" });
  assert.equal(calls[0].init.headers.authorization, undefined);
});

test("pairing reports an expired code by status, and refuses a strange reply", async () => {
  const code = "C".repeat(32);
  const expired = fakeFetch([{ status: 404, body: {} }]);
  await assert.rejects(
    redeemPairing({ apiUrl: "https://x", code, computerName: "L", fetchImpl: expired.fetchImpl }),
    (error) => error.status === 404,
  );
  const odd = fakeFetch([{ status: 201, body: { token: "not-a-token" } }]);
  await assert.rejects(
    redeemPairing({ apiUrl: "https://x", code, computerName: "L", fetchImpl: odd.fetchImpl }),
    /unexpected/,
  );
  await assert.rejects(redeemPairing({ apiUrl: "https://x", code: "short", computerName: "L" }), /Not a pairing code/);
});

test("the device token shape matches what the server issues", () => {
  assert.match(TOKEN, DEVICE_TOKEN);
  assert.doesNotMatch(TOKEN.replace("hd1", "hd2"), DEVICE_TOKEN);
  assert.doesNotMatch(`${TOKEN}.extra`, DEVICE_TOKEN);
});

test("the event id is the idempotency key, so a retry is recognised", async () => {
  const { calls, fetchImpl } = fakeFetch([{ status: 202, body: {} }]);
  await client(fetchImpl).ingest({ schema_version: 4, event_id: "deadbeef" });
  assert.equal(calls[0].init.headers["idempotency-key"], "deadbeef");
});

test("an upload request carries size and type but never the audio", async () => {
  const { calls, fetchImpl } = fakeFetch([
    { status: 200, body: { upload_url: "https://s3/put", object_key: "incoming/abc" } },
  ]);
  const recording = { contentType: "audio/mp4", byteLength: 1234, path: "/secret/path.m4a" };

  await client(fetchImpl).requestUpload(recording, "abc");

  const sent = JSON.parse(calls[0].init.body);
  assert.deepEqual(sent, { event_id: "abc", content_type: "audio/mp4", byte_length: 1234 });
  assert.ok(!calls[0].init.body.includes("/secret/path"), "local paths stay local");
});

const POLICY = {
  upload_url: "https://hearally-prod-media.s3.amazonaws.com/",
  upload_fields: {
    "Content-Type": "audio/mp4",
    "x-amz-checksum-algorithm": "SHA256",
    "x-amz-checksum-sha256": "base64digest=",
    key: "incoming/abc",
    policy: "signed-policy",
    "x-amz-signature": "sig",
  },
};

test("audio goes straight to S3 as a POST against the signed policy", async () => {
  const { calls, fetchImpl } = fakeFetch([{ status: 204, body: {} }]);

  await client(fetchImpl).putAudio(POLICY, Buffer.from("audio"));

  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].url, POLICY.upload_url);
  assert.ok(calls[0].init.body instanceof FormData);
});

test("every policy field is sent back unchanged", async () => {
  const { calls, fetchImpl } = fakeFetch([{ status: 204, body: {} }]);
  await client(fetchImpl).putAudio(POLICY, Buffer.from("audio"));

  const form = calls[0].init.body;
  for (const [name, value] of Object.entries(POLICY.upload_fields)) {
    assert.equal(form.get(name), value, `${name} must match the signed policy`);
  }
});

test("the file is the last field, as S3 requires", async () => {
  const { calls, fetchImpl } = fakeFetch([{ status: 204, body: {} }]);
  await client(fetchImpl).putAudio(POLICY, Buffer.from("audio"));

  const names = [...calls[0].init.body.keys()];
  assert.equal(names.at(-1), "file");
  const file = calls[0].init.body.get("file");
  assert.equal(await file.text(), "audio");
  assert.equal(file.type, "audio/mp4");
});

test("a rejected upload reports the status only", async () => {
  // S3 refuses bytes that break the policy: wrong size, wrong checksum.
  const { fetchImpl } = fakeFetch([{ status: 400, body: {} }]);
  await assert.rejects(client(fetchImpl).putAudio(POLICY, Buffer.from("x")), (error) => {
    assert.equal(error.message, "upload failed with HTTP 400");
    assert.equal(error.retryable, false);
    return true;
  });
});

test("a failed call reports the status and nothing from the response body", async () => {
  const { fetchImpl } = fakeFetch([{ status: 400, body: { detail: "echoed secret content" } }]);
  await assert.rejects(
    client(fetchImpl).ingest({ schema_version: 4, event_id: "abc" }),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.message, "/v1/ingest failed with HTTP 400");
      assert.ok(!error.message.includes("echoed"));
      return true;
    },
  );
});

test("server errors and throttling are retryable; client errors are not", () => {
  assert.equal(new ApiError("x", 500).retryable, true);
  assert.equal(new ApiError("x", 503).retryable, true);
  assert.equal(new ApiError("x", 429).retryable, true);
  assert.equal(new ApiError("x", 401).retryable, false);
  assert.equal(new ApiError("x", 400).retryable, false);
});
