"""Operator tooling for the transcription job on Nebius AI Cloud.

    python transcriber/scripts/nebius_jobs.py registry          # create or find the registry
    python transcriber/scripts/nebius_jobs.py docker-login      # log docker in to it
    python transcriber/scripts/nebius_jobs.py run MANIFEST_URL  # launch a job and wait

Authenticates as the `hearally-transcriber` service account with its
authorized key. The private key never leaves this machine: its path comes from
HEARALLY_NEBIUS_KEY (default ~/.nebius/hearally-transcriber/private.pem), and
no token or key is ever printed. The Nebius CLI does not support Windows, so
this uses the Python SDK (pip install nebius).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import grpc
from nebius.api.nebius.ai.v1 import (
    CreateJobRequest,
    GetJobRequest,
    JobServiceClient,
    JobSpec,
    JobSpec__DiskSpec,
    JobSpec__EnvironmentVariable,
    JobSpec__FileInjection,
)
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import DiskSpec
from nebius.api.nebius.registry.v1 import (
    CreateRegistryRequest,
    ListRegistriesRequest,
    RegistryServiceClient,
    RegistrySpec,
)
from nebius.sdk import SDK

# Identifiers, not secrets. See docs/TRANSCRIPTION.md.
PROJECT = "project-u00gy394kc0029stjb91gt"  # default-project-us-central1
SUBNET = "vpcsubnet-u00e3nbzwz42q2xsze"
SERVICE_ACCOUNT = "serviceaccount-u00kztgzgpqegmph9m"  # hearally-transcriber, editors
PUBLIC_KEY = "publickey-u00b0cw1tt0d07zcmb"  # expires 2026-12-31
REGION = "us-central1"
REGISTRY_NAME = "hearally"
# Without a built image, jobs start from PyTorch's official CUDA 12.8 image
# (CUDA 12.8+ is needed on the Blackwell RTX PRO 6000) with the code injected.
BOOTSTRAP_IMAGE = "pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime"
HERE = Path(__file__).resolve().parents[1]
APP = "/opt/hearally"
IMAGE_NAME = "transcriber"

# One GPU is plenty for Parakeet. The RTX PRO 6000 is the cheapest, but
# us-central1 sometimes has none free ("Not enough resources", 2026-09-25), so
# a job falls back to an H200, then a B200: dearer per hour, and a job lasts
# minutes. All three are within the project's quota.
PLATFORMS = (
    ("gpu-rtx6000", "1gpu-24vcpu-218gb"),  # NVIDIA RTX PRO 6000 (Blackwell)
    ("gpu-h200-sxm", "1gpu-16vcpu-200gb"),  # NVIDIA H200
    ("gpu-b200-sxm", "1gpu-20vcpu-224gb"),  # NVIDIA B200
)
# A batch is cancelled if it runs longer than this, so a stuck job cannot keep
# a GPU billing. Five hours of audio transcribes in minutes; this is headroom.
JOB_TIMEOUT = timedelta(minutes=45)
DISK_BYTES = 128 * 1024**3  # the image is large, and audio is converted to WAV

KEY = Path(
    os.environ.get("HEARALLY_NEBIUS_KEY", Path.home() / ".nebius/hearally-transcriber/private.pem")
)


def sdk() -> SDK:
    return SDK(
        user_agent_prefix="hearally-transcriber-tools/0.1",
        service_account_private_key_file_name=str(KEY),
        service_account_public_key_id=PUBLIC_KEY,
        service_account_id=SERVICE_ACCOUNT,
        # gRPC's bundled roots: the SDK's own Windows lookup wants certifi-win32.
        tls_credentials=grpc.ssl_channel_credentials(),
    )


async def registry_path(s: SDK) -> str:
    """cr.<region>.nebius.cloud/<registry> for the hearally registry, creating it once."""
    registries = RegistryServiceClient(s)
    found = await registries.list(ListRegistriesRequest(parent_id=PROJECT))
    registry = next((r for r in found.items if r.metadata.name == REGISTRY_NAME), None)
    if registry is None:
        op = await registries.create(
            CreateRegistryRequest(
                metadata=ResourceMetadata(parent_id=PROJECT, name=REGISTRY_NAME),
                spec=RegistrySpec(description="Hearally transcription images"),
            )
        )
        await op.wait()
        registry_id = op.resource_id
    else:
        registry_id = registry.metadata.id
    # Images are addressed by the part of the id after "registry-".
    return f"cr.{REGION}.nebius.cloud/{registry_id.split('-', 1)[1]}"


async def docker_login(s: SDK) -> None:
    token = await s.get_token()
    # The token goes to docker on stdin, never on a command line or screen.
    result = subprocess.run(
        ["docker", "login", f"cr.{REGION}.nebius.cloud", "--username", "iam", "--password-stdin"],
        input=token.token,
        text=True,
        capture_output=True,
        check=False,
    )
    print(
        "docker login:",
        "ok" if result.returncode == 0 else f"failed ({result.stderr.strip()[:200]})",
    )


def bootstrap_files() -> list[JobSpec__FileInjection]:
    """The job's code, requirements and start-up script, for running without
    a built image. Nebius places each file read-only at its path (64 KiB max)."""
    injected = [
        (HERE / "bootstrap.sh", f"{APP}/bootstrap.sh"),
        (HERE / "requirements.txt", f"{APP}/requirements.txt"),
        *(
            (path, f"{APP}/src/hearally_transcriber/{path.name}")
            for path in sorted((HERE / "src" / "hearally_transcriber").glob("*.py"))
        ),
    ]
    files = []
    for source, target in injected:
        content = source.read_bytes().replace(b"\r\n", b"\n")
        if len(content) > 64 * 1024:
            raise SystemExit(f"{source.name} is over Nebius's 64 KiB limit for injected files")
        files.append(JobSpec__FileInjection(container_path=target, content=content))
    return files


async def _launch(
    jobs: JobServiceClient,
    manifest_url: str,
    image: str | None,
    platform: str,
    preset: str,
    *,
    on_demand: bool,
) -> tuple[str, str]:
    """One job on one GPU type, waited for. Returns its final state and detail."""
    spec = JobSpec(
        image=image or BOOTSTRAP_IMAGE,
        platform=platform,
        preset=preset,
        subnet_id=SUBNET,
        # Preemptible is cheaper, but the platform may reclaim the machine
        # mid-job (it did, 2026-09-25). The job is safe to rerun from the
        # start, so a reclaimed job is simply retried.
        preemptible=not on_demand,
        restart_attempts=0 if on_demand else 2,
        timeout=JOB_TIMEOUT,
        disk=JobSpec__DiskSpec(type=DiskSpec.DiskType.NETWORK_SSD, size_bytes=DISK_BYTES),
        environment_variables=[
            JobSpec__EnvironmentVariable(name="HEARALLY_MANIFEST_URL", value=manifest_url)
        ],
    )
    if image is None:
        spec.container_command = "bash"
        spec.args = f"{APP}/bootstrap.sh"
        spec.injected_files = bootstrap_files()
    op = await jobs.create(
        CreateJobRequest(
            metadata=ResourceMetadata(
                parent_id=PROJECT, name=f"hearally-transcribe-{int(time.time())}"
            ),
            spec=spec,
        )
    )
    await op.wait()
    job_id = op.resource_id
    print("job", job_id, "on", platform, "on-demand" if on_demand else "preemptible")
    started = time.time()
    state, detail = "UNKNOWN", ""
    while time.time() - started < JOB_TIMEOUT.total_seconds() + 900:
        job = await jobs.get(GetJobRequest(id=job_id))
        state = job.status.state.name
        detail = job.status.state_details.message if job.status.state_details else ""
        print(f"{int(time.time() - started):5d}s  {state}  {detail[:120]}")
        if state in {"COMPLETED", "FAILED", "CANCELLED", "ERROR"}:
            break
        await asyncio.sleep(30)
    return state, detail


async def run(s: SDK, manifest_url: str, image: str | None, *, on_demand: bool = False) -> int:
    """Launch a job and wait, falling back to the next GPU type when one has no
    capacity. With no image, run the stock PyTorch image with the code
    injected (bootstrap.sh installs NeMo at start)."""
    jobs = JobServiceClient(s)
    for platform, preset in PLATFORMS:
        state, detail = await _launch(
            jobs, manifest_url, image, platform, preset, on_demand=on_demand
        )
        if state == "COMPLETED":
            return 0
        if "Not enough resources" not in detail:
            return 1
        print(f"no {platform} free; trying the next GPU type")
    return 1


async def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"registry", "docker-login", "run"}:
        print(__doc__)
        return 2
    s = sdk()
    try:
        if argv[0] == "registry":
            print(await registry_path(s))
        elif argv[0] == "docker-login":
            await docker_login(s)
        else:
            if len(argv) < 2:
                print("run needs the manifest URL (from backend/scripts/transcribe.py)")
                return 2
            # An image argument runs that image; "built" runs ours from the
            # registry; nothing runs the stock image with the code injected.
            if len(argv) > 2 and argv[2] == "built":
                image: str | None = f"{await registry_path(s)}/{IMAGE_NAME}:latest"
            else:
                image = argv[2] if len(argv) > 2 else None
            return await run(s, argv[1], image)
        return 0
    finally:
        await s.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
