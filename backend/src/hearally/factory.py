from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from hearally.audio_services import AudioIngestService, UploadService
from hearally.config import ACKNOWLEDGEMENT_VERSION, ConfigurationError, Settings
from hearally.edge import EdgeVerifier
from hearally.pairing import DynamoPairingRepository, PairingService
from hearally.plaud_import import PlaudImporter, urllib_download
from hearally.plaud_link import DynamoPlaudLinkRepository, PlaudLinkService, urllib_http
from hearally.queueing import SqsProcessingQueue
from hearally.repositories import DynamoIngestRepository
from hearally.scam_library import (
    DynamoScamCardStore,
    EmbeddingClient,
    ScamRetriever,
    load_library,
)
from hearally.secret_cache import current_and_previous_field


def build_pairing_service() -> PairingService:
    import boto3

    table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
    return PairingService(
        DynamoPairingRepository(table), acknowledgement_version=ACKNOWLEDGEMENT_VERSION
    )


def build_plaud_link_service() -> PlaudLinkService:
    import boto3

    table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
    return PlaudLinkService(DynamoPlaudLinkRepository(table))


def _intake() -> tuple[Any, Any, str, PairingService, AudioIngestService]:
    """The pieces every way in shares: table, S3, bucket, pairing and intake."""
    import boto3

    settings = Settings.from_environment()
    bucket = os.getenv("MEDIA_BUCKET", "").strip()
    if not bucket:
        raise ConfigurationError("Hearally is not configured: missing MEDIA_BUCKET")

    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(settings.table_name)
    queue = SqsProcessingQueue(boto3.client("sqs"), settings.processing_queue_url)
    pairing = PairingService(
        DynamoPairingRepository(table), acknowledgement_version=ACKNOWLEDGEMENT_VERSION
    )
    ingest = AudioIngestService(
        DynamoIngestRepository(table),
        queue,
        s3,
        bucket,
        ttl_seconds=settings.ingest_ttl_seconds,
    )
    return table, s3, bucket, pairing, ingest


def build_collector_resources() -> tuple[
    EdgeVerifier, PairingService, UploadService, AudioIngestService
]:
    """Everything the audio collector endpoints need."""
    _, s3, bucket, pairing, ingest = _intake()
    return build_edge_verifier(), pairing, UploadService(s3, bucket), ingest


def build_plaud_importer() -> PlaudImporter:
    """The scheduled Plaud import: the same intake as the collector. It is
    not on the API, so there is no edge to verify."""
    table, s3, bucket, pairing, ingest = _intake()
    repository = DynamoPlaudLinkRepository(table)
    return PlaudImporter(
        repository,
        PlaudLinkService(repository),
        urllib_http,
        urllib_download,
        s3,
        bucket,
        ingest,
        pairing,
        DynamoPairingRepository(table),
    )


def build_public_settings() -> Settings:
    return Settings.from_environment()


@lru_cache(maxsize=1)
def build_edge_verifier() -> EdgeVerifier:
    """Verifier proving a request came through CloudFront.

    The verifier object is built once per container; the token inside it is
    refreshed every five minutes, and the previous version is accepted too, so a
    Terraform rotation of the token does not cause a window of 403s.

    EDGE_REQUIRED exists so local tests can run without the edge in front.
    """
    import boto3

    required = os.getenv("EDGE_REQUIRED", "true").strip().lower() not in {"false", "0", "no"}
    if not required:
        return EdgeVerifier(lambda: "", required=False)

    secret_id = os.getenv("EDGE_TOKEN_SECRET_ID", "")
    return EdgeVerifier(
        current_and_previous_field(boto3.client("secretsmanager"), secret_id, "token")
    )


def build_scam_retriever(table: Any, nebius_key: Callable[[], str]) -> ScamRetriever:
    """Known scam scripts for the judgement (section 7b): the curated library
    shipped with the code, plus cards the weekly refresh has discovered.

    For the transcription worker, which calls judge(..., patterns=this).
    """
    library = load_library(extra=DynamoScamCardStore(table).load())
    return ScamRetriever(library, EmbeddingClient(nebius_key))


def _worker_parts() -> tuple[Any, Any, str, Any]:
    import boto3

    from hearally.nebius_jobs import NebiusJobs
    from hearally.secret_cache import current_field

    settings = Settings.from_environment()
    bucket = os.getenv("MEDIA_BUCKET", "").strip()
    if not bucket:
        raise ConfigurationError("Hearally is not configured: missing MEDIA_BUCKET")
    table = boto3.resource("dynamodb").Table(settings.table_name)
    secrets = boto3.client("secretsmanager")
    secret_id = os.environ["NEBIUS_WORKER_KEY_SECRET_ID"]
    key_id = current_field(secrets, secret_id, "public_key_id")
    private_key = current_field(secrets, secret_id, "private_key")
    jobs = NebiusJobs(lambda: {"public_key_id": key_id(), "private_key": private_key()})
    return table, boto3.client("s3"), bucket, jobs


def build_dispatcher() -> Any:
    import boto3

    from hearally.worker import Dispatcher, DynamoBatchStore, SqsWorkQueue

    table, s3, bucket, jobs = _worker_parts()
    queue = SqsWorkQueue(boto3.client("sqs"), Settings.from_environment().processing_queue_url)
    return Dispatcher(queue, DynamoBatchStore(table), jobs, s3, bucket)


def build_collector() -> Any:
    import boto3

    from hearally.judgement import NemotronClient, judge, judge_recording
    from hearally.secret_cache import current_field
    from hearally.worker import Collector, DynamoAccount, DynamoBatchStore

    table, s3, bucket, jobs = _worker_parts()
    nebius_key = current_field(
        boto3.client("secretsmanager"), os.environ["NEBIUS_SECRET_ID"], "api_key"
    )
    nemotron = NemotronClient(nebius_key, timeout=300.0)
    patterns = build_scam_retriever(table, nebius_key)

    def judged(segments: Any, *, priority_available: bool) -> Any:
        # One conversation at a time: an all-day recording is many of them.
        return judge_recording(
            segments,
            lambda conversation: judge(
                conversation, nemotron, priority_available=priority_available, patterns=patterns
            ),
        )

    from hearally.allies import DynamoAllyRepository, plan_deliveries

    allies = DynamoAllyRepository(table)

    def plan(account: str, items: list[dict[str, Any]], now: float) -> int:
        return plan_deliveries(allies, account, items, now)

    return Collector(
        DynamoBatchStore(table), jobs, s3, bucket, DynamoAccount(table), judged, plan=plan
    )


def _ally_parts() -> tuple[Any, Any]:
    import boto3

    from hearally.allies import DynamoAllyRepository, SesMailer

    table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
    mailer = SesMailer(
        boto3.client("sesv2"),
        from_email=os.getenv("ALLY_FROM_EMAIL", "Hearally <no-reply@hearally.com>"),
        configuration_set=os.getenv("SES_CONFIGURATION_SET") or None,
    )
    return DynamoAllyRepository(table), mailer


def build_ally_service() -> Any:
    from hearally.allies import AllyService

    repo, mailer = _ally_parts()
    return AllyService(repo, mailer)


def build_ally_link_service() -> Any:
    import boto3

    from hearally.allies import AllyLinkService, DynamoAllyRepository

    table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
    return AllyLinkService(DynamoAllyRepository(table))


def build_deliverer() -> Any:
    import boto3

    from hearally.allies import Deliverer
    from hearally.calls import CalleCaller
    from hearally.secret_cache import current_field

    repo, mailer = _ally_parts()
    caller = None
    secret_id = os.getenv("CALLE_SECRET_ID", "").strip()
    if secret_id:
        # Heads-up calls (calls.py). An empty secret fails each call, logged.
        caller = CalleCaller(current_field(boto3.client("secretsmanager"), secret_id, "api_key"))
    return Deliverer(repo, mailer, caller=caller)


def build_ally_phone_service(links: Any) -> Any:
    """An ally's number, proved by a code by text (ally_phone.py)."""
    import boto3

    from hearally.ally_phone import AllyPhoneService
    from hearally.providers.aws_channels import AwsNotifySmsSender
    from hearally.secret_cache import current_field

    configuration = os.getenv("NOTIFY_CONFIGURATION_ID", "").strip()
    pepper_secret = os.getenv("PHONE_PEPPER_SECRET_ID", "").strip()
    if not configuration or not pepper_secret:
        raise ConfigurationError("Hearally is not configured for phone numbers")
    table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
    from hearally.allies import DynamoAllyRepository

    sms = AwsNotifySmsSender(
        boto3.client("pinpoint-sms-voice-v2"),
        configuration_id=configuration,
        template_id=os.getenv("NOTIFY_TEMPLATE_ID", "notify-code-verification-english-001"),
    )
    pepper = current_field(boto3.client("secretsmanager"), pepper_secret, "pepper")
    return AllyPhoneService(links, DynamoAllyRepository(table), sms, pepper)
