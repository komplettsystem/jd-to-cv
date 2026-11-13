"""All configuration comes from environment variables, so the same code runs on a laptop, in a container
and on a server. Nothing here reads a file except the CV."""
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _flag(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = HERE / "data"
    db_path: str = ""
    cv_path: str = str(HERE / "tools" / "sample_cv.md")
    public_cv_path: str = ""                     # what visitors' reports may quote; empty = cv_path
    owner_name: str = "the candidate"            # shown on the landing page and reports
    admin_user: str = "admin"
    admin_password_hash: str = ""                # scrypt hash, see `python -m web.auth hash`
    secret_key: bytes | str = b""
    cookie_secure: bool = False                  # set COOKIE_SECURE=1 behind HTTPS
    demo: bool = False                           # DEMO_READONLY=1: the public read-only demo (fictional data, admin open to readers, no writes)
    main_url: str = ""                           # MAIN_URL: where the real site lives, linked from the demo banner
    demo_url: str = ""                           # DEMO_URL: the read-only demo, linked from the menu of the live site
    repo_url: str = ""                           # REPO_URL: the public repository; the evals page links its documents only when this is set
    trust_proxy: bool = False                    # set TRUST_PROXY=1 behind a load balancer
    sync_jobs: bool = False                      # run jobs inline (tests)
    trace_content: bool = True                   # store full prompts and answers in traces (admin only)
    openrouter_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    aws_region: str = "us-east-1"
    aws_profile: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    legal_name: str = ""                        # who is responsible for the site: shown on the imprint and privacy pages
    legal_address: str = ""                     # postal address, lines separated by \n
    hosting_label: str = "the hosting provider"   # named on the privacy page and in the upload consent text
    linkedin_url: str = ""                      # shown on the contact page
    github_url: str = ""
    contact_email: str = ""                     # an address made for this site, shown on the roadmap and privacy pages
    otlp_endpoint: str = ""                      # optional: also export traces to Langfuse, Phoenix, Logfire...
    default_fit_model: str = "baseline"
    default_judge_model: str = ""                # model for LLM-judge evals; empty = first available
    public_url: str = ""
    allow_local_public: bool = False             # development only: let a local, unpriced model answer visitors
    cv_explicit: bool = False                    # MATCH_CV was set on purpose (otherwise an uploaded public CV, then the sample)
    public_cv_explicit: bool = field(default=False, init=False)  # visitors see CV lines only from a file chosen on purpose

    def __post_init__(self):
        self.public_cv_explicit = bool(self.public_cv_path)

    @classmethod
    def from_env(cls, **override):
        e = os.environ
        data_dir = Path(e.get("MATCH_DATA", HERE / "data"))
        s = cls(
            data_dir=data_dir,
            db_path=e.get("MATCH_DB", str(data_dir / "match.db")),
            cv_path=e.get("MATCH_CV", str(HERE / "tools" / "sample_cv.md")),
            public_cv_path=e.get("MATCH_PUBLIC_CV", ""),
            cv_explicit=bool(e.get("MATCH_CV", "")),
            owner_name=e.get("OWNER_NAME", "the candidate"),
            admin_user=e.get("ADMIN_USER", "admin"),
            admin_password_hash=e.get("ADMIN_PASSWORD_HASH", ""),
            secret_key=e.get("SECRET_KEY", ""),
            cookie_secure=_flag("COOKIE_SECURE"),
            trust_proxy=_flag("TRUST_PROXY"),
            demo=_flag("DEMO_READONLY"),
            main_url=e.get("MAIN_URL", ""),
            repo_url=e.get("REPO_URL", ""),
            demo_url=e.get("DEMO_URL", ""),
            trace_content=_flag("TRACE_CONTENT", True),
            openrouter_api_key=e.get("OPENROUTER_API_KEY", ""),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            aws_region=e.get("AWS_REGION", e.get("AWS_DEFAULT_REGION", "us-east-1")),
            aws_profile=e.get("AWS_PROFILE", ""),
            ollama_url=e.get("OLLAMA_URL", "http://127.0.0.1:11434"),
            legal_name=e.get("LEGAL_NAME", ""),
            legal_address=e.get("LEGAL_ADDRESS", "").replace("\\n", "\n"),
            hosting_label=e.get("HOSTING_LABEL", "the hosting provider"),
            linkedin_url=e.get("LINKEDIN_URL", ""),
            github_url=e.get("GITHUB_URL", ""),
            contact_email=e.get("CONTACT_EMAIL", ""),
            otlp_endpoint=e.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
            default_judge_model=e.get("JUDGE_MODEL", ""),
            public_url=e.get("PUBLIC_URL", ""),
            allow_local_public=_flag("ALLOW_LOCAL_PUBLIC_MODEL"),
        )
        for k, v in override.items():
            setattr(s, k, v)
        if not s.public_cv_path:
            s.public_cv_path = s.cv_path
        if not s.db_path:
            s.db_path = str(Path(s.data_dir) / "match.db")
        return s


def network_key(address):
    """An IPv6 client can use any address in its /64, so the /64 is the unit to limit; an IPv4 address is its own."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else str(ip)
