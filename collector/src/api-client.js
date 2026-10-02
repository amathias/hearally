import { createHash } from "node:crypto";

/**
 * Talks to the Hearally API. Audio never travels through the API itself: it is
 * too large for a request body. Instead the collector asks for a presigned URL,
 * puts the bytes straight into S3, then posts a small event naming the
 * recording. See docs/contracts/ingest-v4.schema.json.
 *
 * Every request carries this computer's device token, issued when it was
 * paired with an account (docs/contracts/collector-pairing.md). The server
 * decides the account from the token, never from anything the collector says.
 */

// hd1.<account>.<device id>.<secret>
export const DEVICE_TOKEN = /^hd1\.[A-Za-z0-9-]{1,64}\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/u;
// What the dashboard shows: 32 URL-safe characters.
export const PAIRING_CODE = /^[A-Za-z0-9_-]{32}$/u;

const TIMEOUT_MS = 30_000;

export class HearallyClient {
  constructor({ apiUrl, deviceToken, fetchImpl = globalThis.fetch }) {
    this.apiUrl = apiUrl.replace(/\/$/u, "");
    this.deviceToken = deviceToken;
    this.fetchImpl = fetchImpl;
  }

  async #post(path, payload, idempotencyKey) {
    const response = await this.fetchImpl(`${this.apiUrl}${path}`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${this.deviceToken}`,
        "content-type": "application/json",
        "idempotency-key": idempotencyKey,
      },
      body: JSON.stringify(payload),
      redirect: "error",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) {
      // The status only. A response body could echo request content, and the
      // collector's output ends up in terminal scrollback and logs.
      throw new ApiError(path, response.status);
    }
    return response.json();
  }

  requestUpload(recording, eventId) {
    return this.#post(
      "/v1/uploads",
      {
        event_id: eventId,
        content_type: recording.contentType,
        byte_length: recording.byteLength,
      },
      eventId,
    );
  }

  /**
   * Whether this computer is still paired, for `status`. Asking for an upload
   * only signs a policy on the server: nothing is stored or reserved, and the
   * upload offered is never used.
   */
  checkConnection() {
    const eventId = createHash("sha256").update("hearally connection check").digest("hex");
    return this.requestUpload({ contentType: "audio/mp4", byteLength: 1 }, eventId);
  }

  /**
   * Sends the audio to S3 as a multipart POST against the signed policy.
   *
   * The policy fields go back exactly as issued; they carry the signature, the
   * exact size and the SHA-256 that S3 checks against the bytes. S3 requires
   * the file to be the last field, and FormData keeps insertion order.
   */
  async putAudio(upload, bytes) {
    const form = new FormData();
    for (const [name, value] of Object.entries(upload.upload_fields ?? {})) {
      form.append(name, value);
    }
    const contentType = upload.upload_fields?.["Content-Type"] ?? "application/octet-stream";
    form.append("file", new Blob([bytes], { type: contentType }));

    const response = await this.fetchImpl(upload.upload_url, { method: "POST", body: form });
    if (!response.ok) throw new ApiError("upload", response.status);
  }

  ingest(event) {
    return this.#post("/v1/ingest", event, event.event_id);
  }
}

/**
 * Trades a one-use pairing code from the dashboard for this computer's device
 * token. Needs no token of its own: holding the code is the authorisation.
 * The code travels in the body, never the URL, so it stays out of logs.
 */
export async function redeemPairing({ apiUrl, code, computerName, fetchImpl = globalThis.fetch }) {
  if (!PAIRING_CODE.test(code ?? "")) throw new TypeError("Not a pairing code");
  const response = await fetchImpl(`${apiUrl.replace(/\/$/u, "")}/v1/pairings/redeem`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ code, computer_name: computerName }),
    redirect: "error",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!response.ok) throw new ApiError("pairing", response.status);
  const result = await response.json();
  if (!DEVICE_TOKEN.test(result?.token ?? "")) throw new Error("Hearally sent back something unexpected");
  return result.token;
}

export class ApiError extends Error {
  constructor(step, status) {
    super(`${step} failed with HTTP ${status}`);
    this.step = step;
    this.status = status;
    // 4xx other than throttling will fail the same way next time; retrying
    // only burns the budget and floods the logs.
    this.retryable = status >= 500 || status === 429;
  }
}
