"""Starting and checking the Parakeet job on Nebius AI Cloud, from Lambda.

The worker's side of transcriber/scripts/nebius_jobs.py: the same job spec,
but it never waits for a job to finish, because finding a GPU alone can take
longer than a Lambda may run. ``launch`` creates the job and returns its id;
``state`` reports where it has got to.

Authentication is the ``hearally-transcriber`` service account with a key
pair kept for this worker alone: its private key lives only in Secrets
Manager (``<name>/nebius-worker-key``) and is written to /tmp for the SDK.
The job's code (transcriber/src, bootstrap.sh, requirements.txt) ships in the
worker's Lambda layer under /opt/transcriber_job and is injected into the
stock PyTorch image, exactly as the operator script does.

The nebius SDK and grpc come from that layer, so they are imported lazily.
"""

from __future__ import annotations

import asyncio
import os
import stat
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

# Identifiers, not secrets. See transcriber/README.md.
PROJECT = "project-u00gy394kc0029stjb91gt"
SUBNET = "vpcsubnet-u00e3nbzwz42q2xsze"
SERVICE_ACCOUNT = "serviceaccount-u00kztgzgpqegmph9m"
BOOTSTRAP_IMAGE = "pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime"
APP = "/opt/hearally"
JOB_FILES = Path(os.environ.get("TRANSCRIBER_JOB_DIR", "/opt/transcriber_job"))
PLATFORMS = (
    ("gpu-rtx6000", "1gpu-24vcpu-218gb"),
    ("gpu-h200-sxm", "1gpu-16vcpu-200gb"),
    ("gpu-b200-sxm", "1gpu-20vcpu-224gb"),
)
JOB_TIMEOUT = timedelta(minutes=45)
DISK_BYTES = 128 * 1024**3
# How long to watch a new job for an immediate "no GPU free" before moving on.
CAPACITY_CHECK_SECONDS = 25


class NoCapacityError(RuntimeError):
    pass


class NebiusJobs:
    def __init__(self, key: Callable[[], dict[str, str]]) -> None:
        """``key`` returns {"public_key_id": ..., "private_key": PEM}."""
        self._key = key

    def _sdk(self) -> Any:
        import grpc
        from nebius.sdk import SDK

        key = self._key()
        path = Path("/tmp/nebius-worker.pem")  # noqa: S108 - Lambda's only writable place
        if not path.exists():
            path.write_text(key["private_key"], encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return SDK(
            user_agent_prefix="hearally-worker/0.1",
            service_account_private_key_file_name=str(path),
            service_account_public_key_id=key["public_key_id"],
            service_account_id=SERVICE_ACCOUNT,
            tls_credentials=grpc.ssl_channel_credentials(),
        )

    def _files(self) -> list[Any]:
        from nebius.api.nebius.ai.v1 import JobSpec__FileInjection

        targets = [
            (JOB_FILES / "bootstrap.sh", f"{APP}/bootstrap.sh"),
            (JOB_FILES / "requirements.txt", f"{APP}/requirements.txt"),
            *(
                (p, f"{APP}/src/hearally_transcriber/{p.name}")
                for p in sorted((JOB_FILES / "src" / "hearally_transcriber").glob("*.py"))
            ),
        ]
        return [
            JobSpec__FileInjection(container_path=t, content=s.read_bytes().replace(b"\r\n", b"\n"))
            for s, t in targets
        ]

    async def _launch(self, manifest_url: str) -> str:
        from nebius.api.nebius.ai.v1 import (
            CreateJobRequest,
            FollowsSpotPriceSpec,
            GetJobRequest,
            JobServiceClient,
            JobSpec,
            JobSpec__DiskSpec,
            JobSpec__EnvironmentVariable,
            PricingModelSpec,
        )
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import DiskSpec

        sdk = self._sdk()
        try:
            jobs = JobServiceClient(sdk)
            for platform, preset in PLATFORMS:
                spec = JobSpec(
                    image=BOOTSTRAP_IMAGE,
                    platform=platform,
                    preset=preset,
                    subnet_id=SUBNET,
                    # Preemptible, retried if reclaimed: the job is safe to rerun.
                    preemptible=True,
                    # Nebius requires a pricing model with preemptible jobs
                    # (since 2026-10): the current spot price, as before.
                    pricing_model=PricingModelSpec(follows_spot_price=FollowsSpotPriceSpec()),
                    restart_attempts=2,
                    timeout=JOB_TIMEOUT,
                    disk=JobSpec__DiskSpec(
                        type=DiskSpec.DiskType.NETWORK_SSD, size_bytes=DISK_BYTES
                    ),
                    environment_variables=[
                        JobSpec__EnvironmentVariable(
                            name="HEARALLY_MANIFEST_URL", value=manifest_url
                        )
                    ],
                    container_command="bash",
                    args=f"{APP}/bootstrap.sh",
                    injected_files=self._files(),
                )
                op = await jobs.create(
                    CreateJobRequest(
                        metadata=ResourceMetadata(
                            parent_id=PROJECT, name=f"hearally-worker-{int(time.time())}"
                        ),
                        spec=spec,
                    )
                )
                job_id = str(op.resource_id)
                # "No GPU free" shows within seconds; anything else is a job
                # that will run, however long it waits for its machine.
                deadline = time.monotonic() + CAPACITY_CHECK_SECONDS
                full = False
                while time.monotonic() < deadline:
                    job = await jobs.get(GetJobRequest(id=job_id))
                    detail = job.status.state_details.message if job.status.state_details else ""
                    if "Not enough resources" in detail:
                        full = True
                        break
                    if job.status.state.name not in {"CREATING", "PROVISIONING", "UNKNOWN"}:
                        break
                    await asyncio.sleep(5)
                if not full:
                    return job_id
            raise NoCapacityError("no GPU type had capacity")
        finally:
            await sdk.close()

    async def _state(self, job_id: str) -> str:
        from nebius.api.nebius.ai.v1 import GetJobRequest, JobServiceClient

        sdk = self._sdk()
        try:
            job = await JobServiceClient(sdk).get(GetJobRequest(id=job_id))
            return str(job.status.state.name)
        finally:
            await sdk.close()

    def ready(self) -> bool:
        """Whether the key is in place: a registered public key's id, and a
        private key. Says nothing about Nebius itself."""
        try:
            key = self._key()
        except Exception:
            return False
        return str(key.get("public_key_id", "")).startswith("publickey-") and bool(
            key.get("private_key")
        )

    def launch(self, manifest_url: str) -> str:
        return asyncio.run(self._launch(manifest_url))

    def state(self, job_id: str) -> str:
        return asyncio.run(self._state(job_id))
