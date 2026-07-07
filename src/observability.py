import logging
import os

import sentry_sdk

log = logging.getLogger("vendorscreen")


def init_sentry(environment):
    """Initialize Sentry error tracking if SENTRY_DSN is configured.

    This is a no-op when SENTRY_DSN is unset, so local dev and any deploy
    without the variable keep working exactly as before. `capture_exception()`
    elsewhere is likewise safe to call whether or not this ran — the SDK simply
    drops events until it is initialized.

    PII is deliberately NOT sent (`send_default_pii=False`): VendorScreen is a
    KYC/AML tool, so we avoid shipping request bodies, headers, cookies, or IPs
    that could carry vendor data to a third-party service.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        log.info("[observability] SENTRY_DSN not set — error tracking disabled")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # Error tracking only by default. Opt into performance tracing by
        # setting SENTRY_TRACES_SAMPLE_RATE (e.g. 0.1) in the environment.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
    )
    log.info("[observability] Sentry initialized (environment=%s)", environment)
    return True
