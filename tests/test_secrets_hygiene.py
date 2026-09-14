"""SEC-1 — pins for the secret-scanning and dependency-audit controls.

These pins guard configuration, not behaviour, and configuration fails
differently from code: it fails SILENTLY and it fails GREEN. A shallow clone
makes gitleaks report "no leaks found" over history it never read; a `*` in
.gitignore makes every ignore assertion pass while ignoring the whole
repository; a regex anchored to one key length keeps matching the test fixture
long after the real key format has moved past it. In each case the instrument
reports success, which is the failure mode this suite has the most scar tissue
about (CLAUDE.md's two standing rules on piped exit codes and on `2 skipped`
meaning a broken instrument rather than a smaller green).

So every pin here is paired with its non-vacuity control:

  * the ignore pins are paired with paths that must NOT be ignored;
  * the gitleaks rule is exercised against strings that must NOT match as well
    as ones that must;
  * the workflow pins assert the two settings whose absence would make the
    scan pass without reading anything (fetch-depth: 0, --log-opts=--all).

The gitleaks BINARY is deliberately not required. It is a Homebrew/Go install,
absent on a fresh checkout and absent on the CI runner until the workflow
installs it, and a test suite that skips when a security control is missing is
a test suite that will skip forever. The custom rule is therefore pinned by
compiling its regex out of .gitleaks.toml with `re` — RE2 and Python agree on
this pattern's syntax — so the rule's INTENT is pinned everywhere pytest runs.

Every `git check-ignore` call passes --no-index, and that flag is load-bearing
twice over. Without it, check-ignore SKIPS paths that are already tracked — so
the "no tracked file matches a credential pattern" pin below would consult a
list of tracked files with an instrument that refuses to answer for tracked
files, and pass unconditionally forever. And the verdict is taken from the
plain exit code rather than from `-v`: in verbose mode check-ignore also
reports NEGATION matches and exits 0 for them, so `!outputs/*.md` would read as
"ignored" for a file that is perfectly committable.

Subprocess style follows tests/test_gate_sh.py: capture_output, text, an
explicit timeout, no check=True, and failure messages carrying both streams.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture(scope="module", autouse=True)
def _requires_git_worktree() -> None:
    """These pins read real git state; skip cleanly outside a checkout.

    Not a silent pass: an installed wheel has no .git, and asserting on
    check-ignore there would fail for a reason that has nothing to do with the
    control being tested.
    """
    proc = _git("rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        pytest.skip("not inside a git work tree")


# ── .gitignore ───────────────────────────────────────────────────────────────

# Credential-shaped paths that must never be stageable. `.env.api` is the rule
# that existed before SEC-1; the rest were all committable until it landed.
MUST_BE_IGNORED = [
    ".env",
    ".env.local",
    ".env.api",
    ".env.production",
    "secret.key",
    "deploy/tls.pem",
    "certs/client.p12",
    # The deployed unit's env file (EnvironmentFile=/etc/vibagent/env,
    # deploy/vibagent.service:16) under the names it takes when copied here.
    "vibagent.env",
    "vibapp.env",
    # A gitleaks report holds the raw secrets it found.
    "gitleaks-report.json",
]

# The control. Without these, a stray `*` in .gitignore would satisfy every
# assertion above while ignoring the entire repository.
MUST_NOT_BE_IGNORED = [
    "src/vib_agent/cli.py",
    "pyproject.toml",
    "config/thresholds.json",
    ".github/workflows/security.yml",
]


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_credential_shaped_paths_are_gitignored(path: str) -> None:
    """A file with a credential-shaped NAME cannot be staged.

    git check-ignore is a rule query, so these paths need not exist.
    """
    proc = _git("check-ignore", "--no-index", path)
    assert proc.returncode == 0, (
        f"{path!r} is NOT gitignored — it could be committed.\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


@pytest.mark.parametrize("path", MUST_NOT_BE_IGNORED)
def test_ignore_rules_are_not_vacuous(path: str) -> None:
    """The ignore rules must not be so broad that they swallow the product."""
    proc = _git("check-ignore", "--no-index", path)
    assert proc.returncode != 0, (
        f"{path!r} IS gitignored — the .gitignore rules are too broad, which "
        f"would make every pin in this module pass vacuously.\nstdout={proc.stdout!r}"
    )


# The credential-shaped NAMES this session added rules for, as basename globs.
# Deliberately NOT "every pattern in .gitignore": several paths are tracked in
# spite of a matching ignore rule ON PURPOSE — the demo report PDFs under
# `outputs/*` and the committed corpus baseline under `scripts/eval/results/` —
# and sweeping those into this pin would make it fail for reasons that have
# nothing to do with credentials.
CREDENTIAL_GLOBS = [
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "vibagent.env",
    "vibapp.env",
    "gitleaks-*.json",
]


def _is_credential_shaped(path: str) -> bool:
    return any(fnmatch(PurePosixPath(path).name, glob) for glob in CREDENTIAL_GLOBS)


def test_no_tracked_file_is_credential_shaped() -> None:
    """Nothing already in the index would be exempt from the SEC-1 rules.

    git consults .gitignore only for UNtracked paths, so a credential-shaped
    file that was committed before the rule existed keeps being tracked and the
    rule never applies to it. It would be invisible to exactly the control that
    is supposed to cover it.
    """
    listed = _git("ls-files")
    assert listed.returncode == 0, listed.stderr
    tracked = [ln for ln in listed.stdout.splitlines() if ln.strip()]
    assert tracked, "git ls-files returned nothing — the pin would be vacuous"
    offenders = [path for path in tracked if _is_credential_shaped(path)]
    assert not offenders, (
        "tracked files have credential-shaped names, so the .gitignore rules "
        f"added at SEC-1 do not apply to them: {offenders}"
    )


@pytest.mark.parametrize(
    "path",
    [
        "deploy/prod.pem",
        "certs/server.key",
        ".env.production",
        "vibagent.env",
        "gitleaks-report.json",
    ],
)
def test_credential_shape_matcher_is_not_vacuous(path: str) -> None:
    """The matcher above must actually match. Otherwise the pin is decorative."""
    assert _is_credential_shaped(path)


@pytest.mark.parametrize("path", ["src/vib_agent/cli.py", "config/thresholds.json", "README.md"])
def test_credential_shape_matcher_does_not_overmatch(path: str) -> None:
    assert not _is_credential_shaped(path)


# ── .gitleaks.toml ───────────────────────────────────────────────────────────


def _gitleaks_config() -> dict:
    path = _ROOT / ".gitleaks.toml"
    assert path.is_file(), "SEC-1: .gitleaks.toml is missing"
    return tomllib.loads(path.read_text())


def test_gitleaks_config_extends_the_default_ruleset() -> None:
    """Custom rules ADD to the upstream ~170; they never replace them."""
    cfg = _gitleaks_config()
    assert cfg.get("extend", {}).get("useDefault") is True, (
        "gitleaks must extend its default ruleset — without useDefault the "
        "custom rule below would be the ONLY rule in force."
    )


def test_gitleaks_allowlist_stays_empty_or_argued() -> None:
    """An allowlist entry is a hole; it may exist, but not anonymously.

    SEC-1 shipped with none. If a later session adds one, it must carry a
    comment saying why the match is not a credential — this pin fails on a
    bare, undocumented entry.
    """
    cfg = _gitleaks_config()
    entries = cfg.get("allowlists", []) or ([cfg["allowlist"]] if "allowlist" in cfg else [])
    if not entries:
        return
    text = (_ROOT / ".gitleaks.toml").read_text()
    assert "#" in text.split("allowlist", 1)[1], (
        "a gitleaks allowlist entry exists with no explanatory comment"
    )


def _anthropic_rule_regex() -> re.Pattern[str]:
    cfg = _gitleaks_config()
    rules = [r for r in cfg.get("rules", []) if r.get("id") == "anthropic-api-key-shape"]
    assert rules, (
        "the anthropic-api-key-shape rule is gone. gitleaks 8.30.1's DEFAULT "
        "ruleset detects only the canonical 108-character key "
        "(sk-ant-api03- + 93 chars + 'AA'); it misses other lengths and the "
        "shorter sk-ant- prefix. Measured at SEC-1. This repo's only real "
        "credential is an Anthropic key, so removing this rule reopens the "
        "one gap that matters most here."
    )
    return re.compile(rules[0]["regex"])


# 40+ characters of key alphabet after the prefix — long enough to be a key.
_BODY = "A1b2C3d4E5f6G7h8I9j0" * 5


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param("sk-ant-api03-" + "x" * 93 + "AA", id="canonical-108"),
        pytest.param("sk-ant-api03-" + _BODY, id="api03-longer-body"),
        pytest.param("sk-ant-" + _BODY, id="legacy-short-prefix"),
        pytest.param('ANTHROPIC_API_KEY="sk-ant-api03-' + _BODY + '"', id="quoted-assignment"),
    ],
)
def test_anthropic_rule_matches_real_key_shapes(sample: str) -> None:
    """The two middle cases are the ones gitleaks' default ruleset MISSES."""
    assert _anthropic_rule_regex().search(sample), (
        "an Anthropic-key-shaped string is not matched by the custom rule"
    )


@pytest.mark.parametrize(
    "sample",
    [
        # RUNBOOK.md:48 and outputs/SESSION_V2WIRE.md:461 — documentation
        # placeholders. If the rule matched these, every scan would be red and
        # the allowlist would grow to compensate.
        pytest.param("ANTHROPIC_API_KEY=sk-ant-...", id="runbook-placeholder"),
        pytest.param("sk-ant-…", id="session-doc-ellipsis"),
        pytest.param("sk-ant-short", id="too-short-to-be-a-key"),
        pytest.param("not a key at all", id="unrelated-text"),
    ],
)
def test_anthropic_rule_does_not_match_placeholders(sample: str) -> None:
    """The non-vacuity control: a rule that matches everything is noise."""
    assert not _anthropic_rule_regex().search(sample), (
        f"the custom rule matches {sample!r}, which is not a credential"
    )


def _resend_rule_regex() -> re.Pattern[str]:
    cfg = _gitleaks_config()
    rules = [r for r in cfg.get("rules", []) if r.get("id") == "resend-api-key-shape"]
    assert rules, (
        "the resend-api-key-shape rule is gone. gitleaks 8.30.1 has NO Resend "
        "rule at all: a bare key on its own line is missed, and so is a "
        "low-entropy one with RESEND_API_KEY= beside it. Measured at EMAIL-1 "
        "and again at SEC-2. RESEND_API_KEY is this product's second real "
        "credential, so removing this rule reopens the gap for it."
    )
    return re.compile(rules[0]["regex"])


# Built from pieces, never written out: this file is tracked, and
# tests/test_email1_egress.py greps every tracked file for a Resend-shaped
# string. A literal canary here would redden that pin and put a matchable
# string into git history -- where the full-history scan would then find it
# forever.
_RE = "re" + "_"


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param(_RE + "123456789_AbCdEfGhIjKlMnOpQrStUvWx", id="id-secret-form"),
        pytest.param(_RE + "8kQvN2mXpL4rT7wZ3yB6cD9fH1jK5nS0", id="flat-32-char-form"),
        pytest.param("RESEND_API_KEY=" + _RE + "123456789_AbCdEfGhIjKlMnOpQr", id="bare-assignment"),
        pytest.param('key = "' + _RE + '123456789_AbCdEfGhIjKlMnOpQr"', id="quoted-assignment"),
    ],
)
def test_resend_rule_matches_real_key_shapes(sample: str) -> None:
    """The first three are shapes the DEFAULT ruleset misses entirely."""
    assert _resend_rule_regex().search(sample), (
        "a Resend-key-shaped string is not matched by the custom rule"
    )


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param("re_export = 1", id="ordinary-identifier"),
        pytest.param("pre_computed_value_that_is_long", id="prefix-mid-word"),
        pytest.param("resend", id="the-provider-name"),
        pytest.param(_RE + "short", id="too-short-to-be-a-key"),
        pytest.param("RESEND_API_KEY=" + _RE + "...", id="documentation-placeholder"),
        pytest.param("test-placeholder-not-a-real-key", id="the-suites-own-fake"),
    ],
)
def test_resend_rule_does_not_match_placeholders(sample: str) -> None:
    """The non-vacuity control. `pre_computed_value_that_is_long` is the one
    that justifies the `\\b`, and the suite's own fake key is the one that
    would otherwise redden every run."""
    assert not _resend_rule_regex().search(sample), (
        "the resend rule matches a string that is not a credential"
    )


def _gitleaks_binary_or_skip() -> str:
    """The Go binary, or a skip that says why.

    Every pin above compiles the rule with Python's `re` and therefore runs on
    every box. This one runs the real scanner, which is the only way to prove
    the CONFIG is loaded and the rule is reachable -- a rule with a typo in its
    `keywords` list compiles fine and never fires.
    """
    found = shutil.which("gitleaks")
    if not found:
        pytest.skip(
            "gitleaks is not installed (brew install gitleaks). The rule's "
            "regex is pinned above without it; this case additionally proves "
            "the scanner LOADS .gitleaks.toml and goes red."
        )
    return found


def _scan_stdin(binary: str, payload: str, report: Path) -> tuple[int, list[dict]]:
    """Pipe `payload` through gitleaks with this repo's config. Always
    `--redact`, so neither the report file nor any failure message can hold a
    secret -- SEC-1's rule for reports applies to tests too."""
    proc = subprocess.run(
        [binary, "stdin", "--no-banner", "--redact",
         "--config", str(_ROOT / ".gitleaks.toml"),
         "--report-format", "json", "--report-path", str(report)],
        input=payload, capture_output=True, text=True, timeout=120,
    )
    findings = json.loads(report.read_text()) if report.is_file() else []
    return proc.returncode, findings


def test_gitleaks_goes_red_on_a_resend_canary(tmp_path: Path) -> None:
    """The scanner, proven able to FAIL. An unproven control is not a control.

    The canary is assembled here and never written to disk inside the
    repository; the report goes to tmp_path, outside it, because a gitleaks
    report contains what it found.
    """
    binary = _gitleaks_binary_or_skip()
    canary = _RE + "123456789" + "_" + "AbCdEfGhIjKlMnOpQrStUvWx"
    code, findings = _scan_stdin(binary, canary + "\n", tmp_path / "canary.json")
    assert code == 1, f"gitleaks did not fail on a Resend-shaped key (exit {code})"
    assert [f.get("RuleID") for f in findings] == ["resend-api-key-shape"], (
        "the canary was caught by a different rule (or not at all) -- "
        f"rules that fired: {[f.get('RuleID') for f in findings]}"
    )
    for finding in findings:
        assert finding.get("Secret") == "REDACTED", "--redact did not redact the report"
        assert finding.get("Match") == "REDACTED", "--redact did not redact the report"


def test_gitleaks_stays_green_on_content_that_only_looks_like_one(tmp_path: Path) -> None:
    """The paired control. Without it, "it went red" could just mean the
    scanner reddens on everything."""
    binary = _gitleaks_binary_or_skip()
    clean = "re_export = 1\npre_computed_value_that_is_long = 2\nresend delivers the link\n"
    code, findings = _scan_stdin(binary, clean, tmp_path / "clean.json")
    assert code == 0 and findings == [], f"false positives: {[f.get('RuleID') for f in findings]}"


def _stripe_webhook_rule_regex() -> re.Pattern[str]:
    cfg = _gitleaks_config()
    rules = [r for r in cfg.get("rules", []) if r.get("id") == "stripe-webhook-secret-shape"]
    assert rules, (
        "the stripe-webhook-secret-shape rule is gone. gitleaks 8.30.1 catches "
        "BOTH Stripe secret keys with its built-in `stripe-access-token` and "
        "misses the webhook signing secret entirely -- measured at BILL-1, four "
        "canaries, one uncovered. That is the half that matters: the signing "
        "secret is what forges a `checkout.session.completed` and grants credits "
        "without a payment, so removing this rule reopens the only Stripe gap "
        "the defaults leave."
    )
    return re.compile(rules[0]["regex"])


# Built from pieces for the same two reasons `_RE` is, and one more: this file is
# tracked, tests/test_bill1_secrets.py greps EVERY tracked file for a
# `whsec_`-shaped string, and a literal here would redden that pin AND enter git
# history, where the full-history scan would find it on every run forever.
_WHSEC = "whsec" + "_"

# Assembled for the SAME reason as `_WHSEC` above, found by the round-4 gate:
# tests/test_bill1_packs.py::TestNoPriceIsAnywhereInTheProduct::
# test_no_stripe_price_identifier_is_tracked greps every tracked file for
# `\bprice_1[A-Za-z0-9]{10,}` -- a Price ID is not a secret, but it is
# configuration that differs between the test and live Stripe accounts, and one
# committed here is one that charges a real card from a test run. BILL-1 built
# its own needle from pieces so the pin file would not redden itself; the
# gitleaks false-positive control below needs the same treatment. The RUNTIME
# value is byte-identical to the literal it replaces, so nothing the scanner
# sees has changed.
_PRICE_ID = "price" + "_" + "1AbCdEfGhIjKlMnOpQrStUv"


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param(_WHSEC + "8kQvN2mXpL4rT7wZ3yB6cD9fH1jK5nS0", id="flat-32-char-form"),
        pytest.param(_WHSEC + "7pR3zK9mW2xY6bQ4nT8vL5cH1jF0dGaS", id="longer-body"),
        pytest.param("STRIPE_WEBHOOK_SECRET=" + _WHSEC + "8kQvN2mXpL4rT7wZ3yB6cD9fH1jK5nS0",
                     id="bare-assignment"),
        pytest.param('secret = "' + _WHSEC + '8kQvN2mXpL4rT7wZ3yB6cD9fH1jK5nS0"',
                     id="quoted-assignment"),
    ],
)
def test_stripe_webhook_rule_matches_real_key_shapes(sample: str) -> None:
    """Every one of these is missed by the DEFAULT ruleset -- there is no
    upstream rule for this prefix at all, which is what BILL-1 measured."""
    assert _stripe_webhook_rule_regex().search(sample), (
        "a Stripe-webhook-secret-shaped string is not matched by the custom rule"
    )


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param(_WHSEC + "tooshort", id="too-short-to-be-a-key"),
        pytest.param("STRIPE_WEBHOOK_SECRET", id="the-variable-name-alone"),
        pytest.param("STRIPE_WEBHOOK_SECRET=" + _WHSEC + "...", id="documentation-placeholder"),
        pytest.param("test-placeholder-not-a-real-webhook-secret", id="the-suites-own-fake"),
        pytest.param("the " + _WHSEC + " prefix has no upstream rule", id="prose-mentioning-it"),
        pytest.param("sk" + "_" + "live" + "_" + "51AbCdEfGhIjKlMnOpQrStUv", id="a-secret-key-not-this-rules-job"),
    ],
)
def test_stripe_webhook_rule_does_not_match_placeholders(sample: str) -> None:
    """The non-vacuity control. The last case is the interesting one: a secret
    KEY must not be claimed by this rule, because the built-in
    `stripe-access-token` already owns it and two rules firing on one string
    makes the canary assertion below ambiguous."""
    assert not _stripe_webhook_rule_regex().search(sample), (
        "the stripe webhook rule matches a string that is not a webhook secret"
    )


def test_gitleaks_goes_red_on_a_stripe_webhook_canary(tmp_path: Path) -> None:
    """The scanner, proven able to FAIL on the shape it used to let through.

    Assembled here and never written to disk inside the repository; the report
    goes to tmp_path, outside it, because a gitleaks report contains what it
    found. The rule id is asserted as a SINGLE-element list on purpose: BILL-1
    measured that no built-in rule fires on this prefix, so anything else in
    that list means either the default ruleset changed under us or this rule is
    catching something it should not.

    THE BODY IS RANDOM-LOOKING ON PURPOSE, and it is the reason this test is
    worth its runtime. LEGAL-1 first wrote the canary as
    `whsec_` + "AbCdEfGhIjKlMnOpQrStUvWxYz012345" -- a valid match for the rule's
    regex, and Python's `re` agrees it matches. gitleaks reported **no leaks
    found, exit 0**: its default allowlist drops sequential runs like a walked
    alphabet, so a canary built from one is silently discarded and the scan comes
    back green while the rule is working perfectly. A green result from that
    canary would have "proved" a control that had never fired. Any replacement
    body must be high-entropy, and must be re-measured against the binary rather
    than against `re`.
    """
    binary = _gitleaks_binary_or_skip()
    canary = _WHSEC + "8kQvN2mXpL4rT7wZ3yB6cD9fH1jK5nS0"
    code, findings = _scan_stdin(binary, canary + "\n", tmp_path / "canary.json")
    assert code == 1, f"gitleaks did not fail on a Stripe webhook secret (exit {code})"
    assert [f.get("RuleID") for f in findings] == ["stripe-webhook-secret-shape"], (
        "the canary was caught by a different rule (or not at all) -- "
        f"rules that fired: {[f.get('RuleID') for f in findings]}"
    )
    for finding in findings:
        assert finding.get("Secret") == "REDACTED", "--redact did not redact the report"
        assert finding.get("Match") == "REDACTED", "--redact did not redact the report"


def test_gitleaks_stays_green_on_stripe_content_that_only_looks_like_one(tmp_path: Path) -> None:
    """The paired control for the rule above. A Price ID is in here because
    BILL-1 measured it as correctly uncovered: a Price ID is configuration and a
    rule for it would train people to ignore findings. It is assembled from
    `_PRICE_ID` rather than written out — see that constant."""
    binary = _gitleaks_binary_or_skip()
    clean = (
        f"{_WHSEC}tooshort = 1\n"
        "STRIPE_WEBHOOK_SECRET is read from the environment\n"
        f"{_PRICE_ID} is a Price ID, not a secret\n"
    )
    code, findings = _scan_stdin(binary, clean, tmp_path / "clean_stripe.json")
    assert code == 0 and findings == [], f"false positives: {[f.get('RuleID') for f in findings]}"



# ── .pre-commit-config.yaml ──────────────────────────────────────────────────


def _gitleaks_hook() -> dict:
    path = _ROOT / ".pre-commit-config.yaml"
    assert path.is_file(), "SEC-1: .pre-commit-config.yaml is missing"
    cfg = yaml.safe_load(path.read_text())
    hooks = [h for repo in cfg["repos"] for h in repo["hooks"] if h["id"] == "gitleaks"]
    assert hooks, "the gitleaks pre-commit hook is gone"
    return hooks[0]


def test_precommit_hook_scans_staged_content_and_redacts() -> None:
    """Three settings, each load-bearing.

    --staged  scopes the scan to this commit (without it the hook re-scans the
              whole repo on every commit and gets disabled for being slow).
    --redact  keeps the blocked secret out of the terminal and the scrollback.
    pass_filenames must be false, or pre-commit appends changed filenames and
              gitleaks reads the first as the repository path.
    """
    hook = _gitleaks_hook()
    args = hook.get("args", [])
    assert "--staged" in args, "the hook must scan staged content"
    assert "--redact" in args, "the hook must not print the secret it blocks"
    assert hook.get("pass_filenames") is False, (
        "pass_filenames must be false or gitleaks misreads a filename as a repo path"
    )


def test_precommit_hook_uses_the_repo_gitleaks_config() -> None:
    """The hook and CI must enforce the SAME rules, including the custom one."""
    args = _gitleaks_hook().get("args", [])
    assert any("--config" in a and ".gitleaks.toml" in a for a in args), (
        "the hook must pass --config=.gitleaks.toml, or it silently runs the "
        "default ruleset and loses the anthropic-api-key-shape rule"
    )


# ── .github/workflows/security.yml ───────────────────────────────────────────


def _security_workflow() -> dict:
    path = _ROOT / ".github" / "workflows" / "security.yml"
    assert path.is_file(), "SEC-1: the security workflow is missing"
    return yaml.safe_load(path.read_text())


def test_security_workflow_runs_on_every_push_and_pull_request() -> None:
    """YAML 1.1 parses a bare `on:` key as the BOOLEAN True, not the string.

    Accept either, or this pin fails for a reason unrelated to the control —
    and worse, a later reader "fixes" the workflow to satisfy the test.
    """
    wf = _security_workflow()
    triggers = wf.get("on", wf.get(True))
    assert triggers is not None, "the workflow declares no triggers"
    assert "push" in triggers, "gitleaks must run on every push"
    assert "pull_request" in triggers, "gitleaks must run on every pull request"
    # No branch filter on push: ci.yml only covers master and evidence-reports,
    # so a secret pushed to any other branch would otherwise go unscanned.
    assert not (triggers.get("push") or {}), (
        "the push trigger must NOT be branch-filtered — every branch is scanned"
    )


def test_secret_scan_reads_full_history_across_all_refs() -> None:
    """The two settings whose absence makes the scan pass without reading.

    A shallow checkout scans one commit; without --log-opts=--all the scan
    covers only the pushed branch's ancestry (measured at SEC-1: that left 4
    commits on unmerged branches unread). Either omission yields a confident
    green over unexamined history.
    """
    text = (_ROOT / ".github" / "workflows" / "security.yml").read_text()
    assert "fetch-depth: 0" in text, "the secret scan needs an unshallow checkout"
    assert "--log-opts=--all" in text or '--log-opts="--all"' in text, (
        "the secret scan must cover every ref, not just HEAD's ancestry"
    )
    assert "--redact" in text, "a CI log must never hold the secret verbatim"


def test_ci_gitleaks_binary_is_pinned_and_checksum_verified() -> None:
    """An unpinned or unverified download is an unaudited binary run in CI."""
    text = (_ROOT / ".github" / "workflows" / "security.yml").read_text()
    assert re.search(r"GITLEAKS_VERSION:\s*\"\d+\.\d+\.\d+\"", text), (
        "the gitleaks version must be pinned exactly"
    )
    assert re.search(r"GITLEAKS_SHA256:\s*\"[0-9a-f]{64}\"", text), (
        "the pinned gitleaks tarball must carry its SHA256"
    )
    assert "sha256sum -c -" in text, "the checksum must actually be VERIFIED, not just stored"


def test_dependency_audit_gates_on_both_the_environment_and_the_pins() -> None:
    """constraints.txt is what production installs; the env is what a dev gets.

    deploy/deploy.sh runs `pip install -c constraints.txt -e '.[web,pdf,dev]'`,
    so a vulnerable PINNED version is the likelier production exposure, and an
    environment-only audit would never see it.
    """
    text = (_ROOT / ".github" / "workflows" / "security.yml").read_text()
    assert "pip-audit" in text, "the dependency audit is gone"
    assert "-r constraints.txt" in text, (
        "the audit must cover constraints.txt — the versions production resolves"
    )
    assert "--ignore-vuln" in text, (
        "the audit must consume .pip-audit-ignore, or accepted exceptions have "
        "no effect and the file is decorative"
    )


# ── .pip-audit-ignore ────────────────────────────────────────────────────────


def test_accepted_vulnerability_entries_are_wellformed_ids() -> None:
    """Every non-comment line must be a single advisory ID.

    The workflow feeds the first token of each line to --ignore-vuln. A prose
    note left uncommented would become a bogus argument, and pip-audit accepts
    unknown IDs silently — so the exception list would appear to work while
    ignoring nothing.
    """
    path = _ROOT / ".pip-audit-ignore"
    assert path.is_file(), "SEC-1: .pip-audit-ignore is missing"
    bad: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not re.fullmatch(r"(GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}|PYSEC-\d{4}-\d+|CVE-\d{4}-\d+)", line):
            bad.append(raw)
    assert not bad, f"malformed advisory ID lines in .pip-audit-ignore: {bad}"
