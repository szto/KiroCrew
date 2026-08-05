# Claude Code 백엔드 + 멀티 계정 프로필 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이 포크에서 Claude Code를 ACP 백엔드로 구동하고, 이름 붙은 계정 프로필을 세션 시작 시 선택할 수 있게 한다.

**Architecture:** 공개 코어에는 `claude_code` 프로바이더의 **소비자 측 배선이 이미 살아 있다**(작성 시점 25개 파일). 제거된 것은 팩토리와 config enum뿐이다. 따라서 `AcpProvider(acp_backend="claude", extra_env={"CLAUDE_CONFIG_DIR": ...})`를 만드는 팩토리 하나와 계정 해석 레이어를 신규 파일로 추가하고, 코어 편집은 작은 hunk로 제한한다. 계정은 새 인증 체계가 아니라 Claude Code가 이미 갖고 있는 `CLAUDE_CONFIG_DIR` 격리에 이름을 붙인 것이다.

**Tech Stack:** Python 3.10+ / asyncio / aiohttp / setuptools · React 18 + TypeScript + Vite + React Query + Framer Motion · pytest(xdist) · vitest

**스펙:** [`design.md`](design.md)

## Global Constraints

- Python ≥ 3.10, 파일 상단에 `from __future__ import annotations`
- 라인 길이 **100자** (black 설정), `black` + `isort`(profile=black) 통과
- flake8: **F401** 미사용 import 금지 · **N806** 함수 지역 변수는 소문자 · **W504** 줄바꿈은 이항 연산자 **앞**
- mypy: 빈 컬렉션에 주석 필수 (`rows: list[str] = []`)
- 로깅은 `import logging` + `logger = logging.getLogger(__name__)`
- pytest `asyncio: mode=strict` → 모든 async 테스트에 `@pytest.mark.asyncio`
- 백엔드 테스트는 **`test/`** 디렉터리에 둔다 (`tests/`가 아니다)
- 상수 금지 규칙: 비즈니스 로직에 하드코딩된 문자열/숫자를 두지 않고, 모든 한계값은 소유 모듈의 상수로 둔다
- 주석은 **이유(불변식·엣지케이스·단위·제약)** 를 적는다. PR 번호·리뷰 라운드·"이전에는/지금은" 서술 금지, 현재 동작을 현재형으로
- 제품명은 **Kiro Crew**(두 단어). 식별자는 각자의 표기 유지(`kirocrew` CLI, `KIROCREW_*`, `kiro_crew` import)
- 프론트엔드 아이콘은 `lucide-react` + `className="lucide-inline"`만. **이모지 금지**, 직접 SVG 금지, `size={N}` 금지
- 사용자 노출 문자열을 하드코딩하지 않는다. 날짜·숫자·정렬은 `src/i18n/format.ts` 경유 + 로케일 명시
- 백엔드 소유 문자열은 i18n 카탈로그 경로가 없으므로 **모든 non-2xx JSON 바디에 `code` 필드**를 싣는다
- 출하 언어 10개: `en`, `zh-CN`, `hi`, `es`, `fr`, `bn`, `pt`, `ru`, `de`, `it`

## 스펙과의 차이 (의도적 정제)

스펙은 코어 편집 대상에 `platform/defaults.py`를 포함했다. 구현 조사 결과 **불필요하다**: `DefaultProviderRegistry.create_factory(cfg)`는 이미 `cfg.create_provider_factory()`를 그대로 반환하므로, 분기를 `create_provider_factory` 안에 두면 같은 결과를 더 적은 접점으로 얻는다. `register_acp_backends()`도 필요 없다 — `AcpProvider(acp_backend="claude")`는 조회할 레지스트리가 없다.

실제 코어 편집 대상:

| 파일 | 편집 |
|---|---|
| `src/kiro_crew/config/loader.py` | provider enum, `AccountConfig`, `agent.account/accounts`, 팩토리 분기 |
| `src/kiro_crew/security.py` | credential 리프 2개 |
| `src/kiro_crew/dashboard/server.py` | 라우트 1줄 |
| `website/src/providers/types.ts` + `adapters/acp.ts` | capability 필드 1개 |
| `website/src/api/client.ts` · `pages/ChatPage.tsx` | 클라이언트 메서드 + 렌더 지점 |

---

## Task 1: 스파이크 — Claude credential 격리 검증 (게이팅)

**이 태스크가 실패하면 Task 2~8의 설계 전제가 무너진다. 반드시 먼저 실행한다.**

작성 시점 이 머신의 Claude Code 2.1.222는 credential을 두 곳에 갖고 있다: `~/.claude/.credentials.json`(mode 600, 유효한 `claudeAiOauth`)과 macOS Keychain 항목 `Claude Code-credentials`. `CLAUDE_CONFIG_DIR`을 바꿨을 때 credential이 따라가야 계정 격리가 성립한다. Keychain이 이기면 두 프로필이 같은 계정을 공유한다.

**Files:**
- Modify: `docs/task-specs/2026/08/claude-code-accounts/plan.md` (이 태스크의 "결과" 절)

**Interfaces:**
- Consumes: 없음
- Produces: go/no-go 판정. `CLAUDE_CONFIG_DIR`이 credential을 격리하면 Task 2~8을 계획대로 진행. 격리하지 않으면 **중단하고 사용자에게 보고** — 계정 프로필의 의미를 API 키 주입으로 재정의하는 스펙 개정이 필요하다.

- [x] **Step 1: 전제조건 설치 확인**

```bash
command -v claude && claude --version
command -v claude-agent-acp || npm i -g @agentclientprotocol/claude-agent-acp
command -v claude-agent-acp
```

기대: `claude`와 `claude-agent-acp` 둘 다 경로가 나온다.

- [x] **Step 2: 빈 config dir로 인증 상태를 관찰**

기존 로그인을 절대 건드리지 않도록 임시 디렉터리를 쓴다.

```bash
PROBE="$(mktemp -d)/claude-probe"
mkdir -p "$PROBE"
CLAUDE_CONFIG_DIR="$PROBE" claude -p "reply with the single word: ok" 2>&1 | head -20
echo "--- probe dir contents ---"
ls -la "$PROBE"
```

판정:
- **격리 성공** = 인증을 요구하거나 미로그인으로 실패한다 (기존 `~/.claude` 토큰을 쓰지 않았다는 증거)
- **격리 실패** = `ok`를 정상 응답한다 → Keychain 또는 다른 전역 소스에서 토큰을 가져왔다는 뜻

- [x] **Step 3: 대조군 — 기본 config dir은 정상 동작하는지**

```bash
claude -p "reply with the single word: ok" 2>&1 | head -5
```

기대: `ok`. Step 2가 실패했는데 이것도 실패하면 판정이 아니라 환경 문제다.

- [x] **Step 4: 결과를 이 파일에 기록**

아래 "결과" 절에 판정과 Step 2 출력의 핵심 줄을 적는다. 추가 마크다운 파일을 만들지 않는다(`AGENTS.md` 규칙).

- [x] **Step 5: 커밋**

```bash
git add docs/task-specs/2026/08/claude-code-accounts/plan.md
git commit -m "docs: record Claude credential isolation spike result"
```

### 결과

**판정: read/fallback 방향은 격리 성공. write 방향은 미검증. 조건부 GO.**

**증명된 것 (read/fallback 방향):** Step 2 (`CLAUDE_CONFIG_DIR`을 새 `mktemp -d`
디렉터리로 지정하고 실행):

```
Not logged in · Please run /login
```

probe 디렉터리에는 `.credentials.json`이 전혀 생성되지 않았다 (`.claude.json`,
`backups/`, `projects/`, `sessions/`만 존재) — Keychain에서 토큰을 끌어와 로컬에
백필하지도 않았다는 뜻이다.

Step 3 (대조군, 기본 `~/.claude`):

```
ok
```

`~/.claude/.credentials.json`의 mtime은 이 스파이크의 모든 명령 실행 이전 시각을
유지했고, probe 디렉터리 재조회에서도 credentials 파일은 없었다 — 실제 로그인은
건드리지 않았다.

이 두 결과는 **empty `CLAUDE_CONFIG_DIR`이 Keychain의 로그인을 조용히 물려받지
않는다**는 것만 증명한다: 격리된 디렉터리를 읽을 때 전역 Keychain으로 fallback하지
않는다는 read 방향.

**증명되지 않은 것 (write 방향):** 두 번째 `CLAUDE_CONFIG_DIR` 아래에서 실제로
`claude login`을 수행했을 때, 그 로그인이 공유 Keychain 서비스 항목
(`Claude Code-credentials`)에 쓰여서 첫 번째 계정의 로그인을 덮어쓰거나 두 계정이
같은 Keychain 항목을 공유(aliasing)하게 되는지는 이 스파이크로 확인하지 못했다.
실제 로그인을 건드리지 말라는 명시적 제약 때문에 이 방향은 두 번째 실계정 없이는
안전하게 실행할 수 없다 — 이번 스코프에서 달성 가능한 최선은 read 방향까지다.
멀티 계정 설계 전체가 의존하는 전제("두 계정이 동시에 독립적으로 로그인 상태를
유지한다")는 바로 이 미검증 절반이다.

**GO의 범위:** Task 2, 3, 4, 6 (순수 config/해석/API 로직, 실제 동시 로그인을
요구하지 않음)은 무조건 진행한다. **두 계정이 동시에 로그인된 상태로 동작한다는
것에 의존하는 어떤 작업도**, 아래 write 방향 테스트가 통과하기 전에는 그 결과를
전제로 삼지 않는다.

**후속 필수 테스트 (사람이 두 번째 Claude 계정을 확보했을 때 실행):**

```bash
# 1) 첫 번째 계정으로 이미 로그인된 상태를 확인 (기존 ~/.claude 또는 profile A)
CLAUDE_CONFIG_DIR="$DIR_A" claude -p "reply with the single word: ok"   # 기대: ok

# 2) 두 번째 config dir에서 별도 계정으로 실제 로그인
CLAUDE_CONFIG_DIR="$DIR_B" claude login    # 두 번째 계정으로 인터랙티브 로그인
CLAUDE_CONFIG_DIR="$DIR_B" claude -p "reply with the single word: ok"   # 기대: ok

# 3) 첫 번째 디렉터리가 여전히 독립적으로 인증되는지 재확인
CLAUDE_CONFIG_DIR="$DIR_A" claude -p "reply with the single word: ok"   # 기대: 여전히 ok, 계정 A로
```

판정: 3번이 계속 계정 A로 `ok`를 반환하면 write 방향도 격리된다 (GO 무조건 확정).
3번이 실패하거나, 계정 A의 신원이 계정 B로 바뀌어 응답하면, 공유 Keychain 항목이
두 번째 로그인에 덮어써졌다는 뜻이다 — 이 경우 멀티 계정 설계는 계정 프로필을
API 키 주입으로 재정의하는 스펙 개정이 필요하다.

**Step 1 관련 기록된 전제조건 (Task 5 차단):** `npm i -g
@agentclientprotocol/claude-agent-acp` 전역 설치는 `/usr/local`이 root 소유라
`EACCES`로 실패했다. 이 스파이크는 세션 스코프 임시 `--prefix`로 설치해 바이너리
해석만 확인했고, 그 prefix는 세션 종료와 함께 이미 사라졌다 — `claude-agent-acp`는
현재 이 머신에서 상시 `PATH`에 있지 않다. Task 5의 live-boot 검증은 `/usr/local`
소유권을 사용자에게 넘기거나 사용자 소유 Node 툴체인(nvm 등)을 도입해 durable
global install을 확보하기 전에는 진행할 수 없다.

전체 명령/출력은 `.superpowers/sdd/plan/task-1-report.md`에 기록했다.

---

## Task 2: `accounts.py` — 계정 프로필 해석

**Files:**
- Create: `src/kiro_crew/accounts.py`
- Test: `test/test_accounts.py`

**Interfaces:**
- Consumes: 없음 (순수 로직. `cfg`는 `TYPE_CHECKING` 아래에서만 import해 순환을 피한다)
- Produces:
  - `DEFAULT_CLAUDE_CONFIG_DIR: str = "~/.claude"`
  - `CREDENTIALS_LEAF: str = ".credentials.json"`
  - `CODE_ACCOUNT_UNKNOWN: str = "account_unknown"`
  - `CODE_ACCOUNT_NOT_LOGGED_IN: str = "account_not_logged_in"`
  - `class AccountError(Exception)` — 속성 `code: str`
  - `@dataclass(frozen=True) class ResolvedAccount` — `name: str`, `config_dir: Path`, `logged_in: bool`
  - `resolve_account(cfg: KiroCrewConfig, name: str | None = None) -> ResolvedAccount`
  - `list_accounts(cfg: KiroCrewConfig) -> list[ResolvedAccount]`

- [ ] **Step 1: 실패하는 테스트를 작성**

`test/test_accounts.py`:

```python
"""Account-profile resolution: name -> CLAUDE_CONFIG_DIR."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kiro_crew.accounts import (
    CODE_ACCOUNT_NOT_LOGGED_IN,
    CODE_ACCOUNT_UNKNOWN,
    AccountError,
    list_accounts,
    resolve_account,
)


class _Agent:
    def __init__(self, account: str = "", accounts: dict | None = None) -> None:
        self.account = account
        self.accounts = accounts or {}


class _Cfg:
    def __init__(self, agent: _Agent) -> None:
        self.agent = agent


class _Acct:
    def __init__(self, config_dir: str) -> None:
        self.config_dir = config_dir


def _login(dir_path: Path) -> None:
    """Write a credentials file shaped like a logged-in Claude Code config dir."""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "x", "refreshToken": "y"}})
    )


def test_no_accounts_block_resolves_implicit_default(tmp_path, monkeypatch):
    """A config with no accounts block still works: one implicit profile on ~/.claude."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")

    resolved = resolve_account(_Cfg(_Agent()))

    assert resolved.name == "default"
    assert resolved.config_dir == tmp_path / ".claude"
    assert resolved.logged_in is True


def test_named_account_resolves_its_config_dir(tmp_path):
    work = tmp_path / "work"
    _login(work)
    cfg = _Cfg(_Agent(account="work", accounts={"work": _Acct(str(work))}))

    resolved = resolve_account(cfg)

    assert resolved.name == "work"
    assert resolved.config_dir == work
    assert resolved.logged_in is True


def test_explicit_name_argument_outranks_config(tmp_path):
    """The per-session pick wins over agent.account."""
    a, b = tmp_path / "a", tmp_path / "b"
    _login(a)
    _login(b)
    cfg = _Cfg(_Agent(account="a", accounts={"a": _Acct(str(a)), "b": _Acct(str(b))}))

    assert resolve_account(cfg, "b").config_dir == b


def test_unknown_account_raises_with_code(tmp_path):
    cfg = _Cfg(_Agent(accounts={"work": _Acct(str(tmp_path / "work"))}))

    with pytest.raises(AccountError) as exc:
        resolve_account(cfg, "nope")

    assert exc.value.code == CODE_ACCOUNT_UNKNOWN


def test_missing_credentials_is_not_logged_in(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = _Cfg(_Agent(account="empty", accounts={"empty": _Acct(str(empty))}))

    assert resolve_account(cfg).logged_in is False


def test_credentials_without_oauth_key_is_not_logged_in(tmp_path):
    """An MCP-only credentials file is not an account login."""
    d = tmp_path / "mcp-only"
    d.mkdir()
    (d / ".credentials.json").write_text(json.dumps({"mcpOAuth": {"slack": {}}}))
    cfg = _Cfg(_Agent(account="mcp-only", accounts={"mcp-only": _Acct(str(d))}))

    assert resolve_account(cfg).logged_in is False


def test_corrupt_credentials_is_not_logged_in(tmp_path):
    d = tmp_path / "corrupt"
    d.mkdir()
    (d / ".credentials.json").write_text("{not json")
    cfg = _Cfg(_Agent(account="corrupt", accounts={"corrupt": _Acct(str(d))}))

    assert resolve_account(cfg).logged_in is False


def test_empty_config_dir_falls_back_to_claude_default(tmp_path, monkeypatch):
    """A profile that names no directory means the default login, not an error."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")
    cfg = _Cfg(_Agent(account="bare", accounts={"bare": _Acct("")}))

    assert resolve_account(cfg).config_dir == tmp_path / ".claude"


def test_tilde_in_config_dir_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / "custom")
    cfg = _Cfg(_Agent(account="c", accounts={"c": _Acct("~/custom")}))

    assert resolve_account(cfg).config_dir == tmp_path / "custom"


def test_list_accounts_reports_every_profile(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _login(a)
    b.mkdir()
    cfg = _Cfg(_Agent(accounts={"a": _Acct(str(a)), "b": _Acct(str(b))}))

    rows = list_accounts(cfg)

    assert {r.name for r in rows} == {"a", "b"}
    assert {r.name: r.logged_in for r in rows} == {"a": True, "b": False}


def test_list_accounts_with_no_block_reports_the_implicit_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")

    rows = list_accounts(_Cfg(_Agent()))

    assert [r.name for r in rows] == ["default"]


def test_not_logged_in_code_is_available_for_callers():
    """The API layer reports this code; keep it exported from one place."""
    assert CODE_ACCOUNT_NOT_LOGGED_IN == "account_not_logged_in"
```

- [ ] **Step 2: 실패를 확인**

```bash
python -m pytest test/test_accounts.py -v -n0
```

기대: `ModuleNotFoundError: No module named 'kiro_crew.accounts'` 로 전부 FAIL.

- [ ] **Step 3: 최소 구현**

`src/kiro_crew/accounts.py`:

```python
"""Claude account profiles — a name bound to a ``CLAUDE_CONFIG_DIR``.

Claude Code isolates its own state (credentials, settings, project history) per
``CLAUDE_CONFIG_DIR``, so a profile needs no credential handling of its own:
pointing the adapter at a different directory IS the account switch.

The implicit ``default`` profile resolves to Claude Code's own default directory,
so a config with no ``accounts`` block keeps working against the user's existing
login with no setup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # circular import: config.loader -> providers -> accounts
    from kiro_crew.config.loader import KiroCrewConfig

logger = logging.getLogger(__name__)

# Claude Code's own default config directory. Both a profile that names no
# directory and the implicit profile resolve here.
DEFAULT_CLAUDE_CONFIG_DIR = "~/.claude"

# Name of the implicit profile a config with no ``accounts`` block gets. Not a
# reserved word: an explicit profile may also be called this.
DEFAULT_ACCOUNT_NAME = "default"

# Claude Code writes its OAuth tokens to this leaf inside a config dir. Reading it
# directly (rather than through the shared file gate) is the same pattern
# ``kiro_usage_api`` uses for the kiro-cli token store: the gate exists to stop the
# AGENT's file tools, not the gateway's own audited readers.
CREDENTIALS_LEAF = ".credentials.json"

# The key Claude Code stores the account OAuth grant under. A credentials file
# holding only ``mcpOAuth`` entries is an MCP-server login, NOT an account login,
# so presence of the file alone is not a sufficient signal.
_OAUTH_KEY = "claudeAiOauth"

# Machine-readable reasons for a non-2xx body. Backend-owned strings have no i18n
# catalog path, so the code is what the dashboard branches on.
CODE_ACCOUNT_UNKNOWN = "account_unknown"
CODE_ACCOUNT_NOT_LOGGED_IN = "account_not_logged_in"


class AccountError(Exception):
    """An account could not be resolved.

    ``code`` is the machine-readable reason a caller puts in its JSON body; the
    message is for logs and is not translated.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedAccount:
    """A profile resolved to an absolute directory plus its login state."""

    name: str
    config_dir: Path
    logged_in: bool


def _config_dir_for(raw: str) -> Path:
    """Expand a profile's configured directory, defaulting to Claude Code's own."""
    return Path(raw or DEFAULT_CLAUDE_CONFIG_DIR).expanduser()


def _is_logged_in(config_dir: Path) -> bool:
    """Whether *config_dir* holds an account OAuth grant.

    Never returns the token or logs its contents — only the boolean. A missing,
    unreadable, or non-JSON file means "not logged in" rather than an error: the
    caller's job is to tell the user to run ``claude login``, not to distinguish
    the ways a credentials file can be absent.
    """
    path = config_dir / CREDENTIALS_LEAF
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get(_OAUTH_KEY))


def resolve_account(cfg: KiroCrewConfig, name: str | None = None) -> ResolvedAccount:
    """Resolve the account to run on, highest precedence first.

    1. *name* — the caller's explicit per-session pick.
    2. ``cfg.agent.account`` — the configured default.
    3. the implicit ``default`` profile on Claude Code's own config dir.

    Raises :class:`AccountError` with ``CODE_ACCOUNT_UNKNOWN`` when a name is
    given that no profile declares. A resolved-but-not-logged-in profile is
    returned with ``logged_in=False`` rather than raising: listing it is
    legitimate, and only the session-start path treats it as fatal.
    """
    accounts = getattr(cfg.agent, "accounts", {}) or {}
    requested = name or getattr(cfg.agent, "account", "") or ""

    if not requested:
        if not accounts:
            config_dir = _config_dir_for("")
            return ResolvedAccount(
                name=DEFAULT_ACCOUNT_NAME,
                config_dir=config_dir,
                logged_in=_is_logged_in(config_dir),
            )
        requested = next(iter(accounts))

    if accounts and requested not in accounts:
        raise AccountError(
            CODE_ACCOUNT_UNKNOWN,
            f"no account profile named {requested!r}",
        )

    entry = accounts.get(requested)
    config_dir = _config_dir_for(getattr(entry, "config_dir", "") if entry else "")
    return ResolvedAccount(
        name=requested,
        config_dir=config_dir,
        logged_in=_is_logged_in(config_dir),
    )


def list_accounts(cfg: KiroCrewConfig) -> list[ResolvedAccount]:
    """Every declared profile, or the single implicit default when none are.

    Declaration order is preserved so the dashboard dropdown is stable across
    reloads instead of reordering on dict iteration.
    """
    accounts = getattr(cfg.agent, "accounts", {}) or {}
    if not accounts:
        return [resolve_account(cfg)]
    return [resolve_account(cfg, name) for name in accounts]
```

- [ ] **Step 4: 통과를 확인**

```bash
python -m pytest test/test_accounts.py -v -n0
```

기대: 12개 PASS.

- [ ] **Step 5: 포맷·린트·타입 게이트**

```bash
black src/kiro_crew/accounts.py test/test_accounts.py
isort src/kiro_crew/accounts.py test/test_accounts.py
flake8 src/kiro_crew/accounts.py test/test_accounts.py
mypy src/kiro_crew/accounts.py
```

기대: 모두 무출력(통과).

- [ ] **Step 6: 커밋**

```bash
git add src/kiro_crew/accounts.py test/test_accounts.py
git commit -m "feat: resolve named Claude account profiles to config dirs"
```

---

## Task 3: config — provider enum과 계정 필드

**Files:**
- Modify: `src/kiro_crew/config/loader.py`
  - `PROVIDER_ACP` / `PROVIDER_CLAUDE_CODE` 상수 + `AccountConfig` 데이터클래스 신규 — **`@dataclass class AgentConfig`(약 730행) 바로 앞**에 둔다. `DEFAULT_CWD_ALLOWED_ROOTS` 블록 뒤가 자리다.
  - `AgentConfig.provider` 필드 (현재 `enum=["acp"]`, 약 754행)
  - `AgentConfig`에 `account` / `accounts` 필드 추가
  - `AgentConfig(...)` 생성 지점 (약 4318행)

**배치가 중요하다.** `provider` 필드가 `default=PROVIDER_ACP`를 쓰는데, 필드 default는 클래스 본문 실행 시점에 **런타임으로 평가**된다. 상수를 `AgentConfig` 뒤(예: `WorkspaceConfig` 근처)에 두면 import 시점에 `NameError`가 난다. `from __future__ import annotations`(12행)는 어노테이션만 지연시키고 default 표현식은 지연시키지 않는다. `AccountConfig`도 같은 자리에 함께 둔다 — 어노테이션은 지연 평가되어 뒤에 있어도 동작하지만, 한 기능의 config 표면을 흩뿌리지 않는다.
- Test: `test/test_claude_code_config.py`

**Interfaces:**
- Consumes: `kiro_crew.accounts` (Task 2) — 이 태스크는 직접 import하지 않지만 필드 이름(`config_dir`)이 `resolve_account`가 읽는 것과 일치해야 한다
- Produces:
  - `PROVIDER_ACP: str = "acp"`, `PROVIDER_CLAUDE_CODE: str = "claude_code"`
  - `@dataclass class AccountConfig` — `config_dir: str = ""`
  - `AgentConfig.account: str`, `AgentConfig.accounts: dict[str, AccountConfig]`
  - `_parse_accounts(raw: dict) -> dict[str, AccountConfig]`

- [ ] **Step 1: 실패하는 테스트를 작성**

`test/test_claude_code_config.py`:

```python
"""Config surface for the claude_code provider and its account profiles."""

from __future__ import annotations

import json
import unittest.mock
from pathlib import Path

from kiro_crew.config.loader import (
    PROVIDER_ACP,
    PROVIDER_CLAUDE_CODE,
    AccountConfig,
    KiroCrewConfig,
)


def _load(tmp_path: Path, payload: dict) -> KiroCrewConfig:
    """Load *payload* as the active config.

    ``KiroCrewConfig.load()`` takes no path — it reads the data home — so the
    repo-wide pattern is to patch ``config_path`` instead of passing a file.
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=path):
        return KiroCrewConfig.load()


def test_provider_field_metadata_lists_both_providers():
    """The JSON schema is generated from this metadata; a missing enum value makes
    validation log a violation and silently fall back to the default."""
    from kiro_crew.config.loader import AgentConfig

    meta = AgentConfig.__dataclass_fields__["provider"].metadata
    assert meta["enum"] == [PROVIDER_ACP, PROVIDER_CLAUDE_CODE]


def test_claude_code_provider_survives_a_config_round_trip(tmp_path):
    """An enum violation would be logged and silently replaced by the default."""
    cfg = _load(
        tmp_path,
        {
            "agent": {
                "provider": "claude_code",
                "account": "work",
                "accounts": {"work": {"config_dir": "~/.claude-work"}},
            }
        },
    )

    assert cfg.agent.provider == PROVIDER_CLAUDE_CODE
    assert cfg.agent.account == "work"
    assert cfg.agent.accounts["work"].config_dir == "~/.claude-work"
    assert isinstance(cfg.agent.accounts["work"], AccountConfig)


def test_absent_account_block_defaults_to_empty(tmp_path):
    cfg = _load(tmp_path, {"agent": {"provider": PROVIDER_ACP}})

    assert cfg.agent.account == ""
    assert cfg.agent.accounts == {}


def test_account_entry_without_config_dir_is_accepted(tmp_path):
    """A bare profile means "the default login" — accounts.py resolves it."""
    cfg = _load(tmp_path, {"agent": {"accounts": {"bare": {}}}})

    assert cfg.agent.accounts["bare"].config_dir == ""


def test_non_dict_account_entry_is_dropped_not_fatal(tmp_path):
    """A hand-edited config must not crash the gateway at boot."""
    cfg = _load(tmp_path, {"agent": {"accounts": {"bad": "oops", "ok": {}}}})

    assert "bad" not in cfg.agent.accounts
    assert "ok" in cfg.agent.accounts
```

- [ ] **Step 2: 실패를 확인**

```bash
python -m pytest test/test_claude_code_config.py -v -n0
```

기대: `ImportError: cannot import name 'PROVIDER_ACP'`.

- [ ] **Step 3: `AccountConfig`와 상수를 추가**

`src/kiro_crew/config/loader.py`, `DEFAULT_CWD_ALLOWED_ROOTS` 블록 뒤 · `@dataclass class AgentConfig`(약 730행) **바로 앞**에 삽입 (위 "배치가 중요하다" 참조):

```python
# Provider ids. ``claude_code`` drives claude-agent-acp through the ACP client's
# ``ACP_BACKEND_CLAUDE`` seam — it is not a second transport, just a second backend
# behind the same AcpProvider.
PROVIDER_ACP = "acp"
PROVIDER_CLAUDE_CODE = "claude_code"


@dataclass
class AccountConfig:
    """One Claude account profile.

    ``config_dir`` becomes ``CLAUDE_CONFIG_DIR`` for sessions on this account, which
    is what isolates its credentials and history. Empty means Claude Code's own
    default directory, so a profile can name an account without relocating it.
    """

    config_dir: str = field(
        default="",
        metadata=_meta(
            "Config Directory",
            "CLAUDE_CONFIG_DIR for this account. Empty uses Claude Code's default.",
        ),
    )
```

- [ ] **Step 4: `_parse_accounts` 헬퍼를 추가**

`_migrate_workspaces`(약 2765행) 근처에 삽입:

```python
def _parse_accounts(raw_accounts: dict) -> dict[str, AccountConfig]:
    """Build ``AccountConfig`` entries, skipping malformed ones.

    A hand-edited config must not abort boot: a non-dict entry is dropped with a
    warning rather than raised, matching how the rest of the loader degrades.
    """
    parsed: dict[str, AccountConfig] = {}
    if not isinstance(raw_accounts, dict):
        return parsed
    for name, entry in raw_accounts.items():
        if not isinstance(entry, dict):
            logger.warning("config: dropping malformed account profile %r", name)
            continue
        parsed[name] = AccountConfig(config_dir=str(entry.get("config_dir", "")))
    return parsed
```

- [ ] **Step 5: `provider` enum을 열고 계정 필드를 추가**

`AgentConfig`의 `provider` 필드(약 754행)를 교체:

```python
    provider: str = field(
        default=PROVIDER_ACP,
        metadata=_meta(
            "Provider",
            "LLM provider backend (KiroACP / kiro-cli, or Claude Code via claude-agent-acp).",
            enum=[PROVIDER_ACP, PROVIDER_CLAUDE_CODE],
        ),
    )
    account: str = field(
        default="",
        metadata=_meta(
            "Account",
            "Named account profile for the claude_code provider. Empty uses the first "
            "declared profile, or Claude Code's default login when none are declared.",
        ),
    )
    accounts: dict[str, AccountConfig] = field(
        default_factory=dict,
        metadata=_meta("Accounts", "Named Claude account profiles."),
    )
```

- [ ] **Step 6: 생성 지점을 배선**

`AgentConfig(` 생성(약 4318행)의 `provider=` 줄 아래에 추가:

```python
                account=agent_data.get("account", ""),
                accounts=_parse_accounts(agent_data.get("accounts", {})),
```

- [ ] **Step 7: 통과를 확인**

```bash
python -m pytest test/test_claude_code_config.py -v -n0
```

기대: 5개 PASS.

- [ ] **Step 8: 기존 config 테스트가 깨지지 않았는지 확인**

```bash
python -m pytest test/ -k "config" -q
python -m pytest tests/test_config_roundtrip.py -q -n0
```

기대: 회귀 없음. `config-baseline.json`이 스키마 스냅샷을 고정한다면 실패가 나온다 — 그 경우 다음 단계로.

- [ ] **Step 9: config 베이스라인을 재생성 (Step 8이 스냅샷 불일치로 실패한 경우에만)**

```bash
python3 scripts/generate_config_baseline.py
git diff --stat config-baseline.json
```

새 필드와 넓어진 enum만 diff에 나타나는지 확인한다. 다른 변화가 있으면 멈추고 조사한다.

- [ ] **Step 10: 게이트 후 커밋**

```bash
black src/kiro_crew/config/loader.py test/test_claude_code_config.py
isort src/kiro_crew/config/loader.py test/test_claude_code_config.py
flake8 src/kiro_crew/config/loader.py test/test_claude_code_config.py
mypy src/kiro_crew
git add src/kiro_crew/config/loader.py test/test_claude_code_config.py config-baseline.json
git commit -m "feat: admit claude_code provider and account profiles in config"
```

---

## Task 4: security — credential 리프를 파일 게이트에서 차단

`~/.claude/.credentials.json`은 계정 OAuth 토큰과 연결된 MCP 서버별 베어러 토큰을 함께 담는다. `_SENSITIVE_HOME_DIRS`는 이미 `.aws`, `.ssh`, `.netrc`, `.midway`, `.local/share/kiro-cli`를 전부 차단하고 있으므로, 추가는 기존 원칙의 확장이다.

**디렉터리 전체가 아니라 리프만** 분류한다. `~/.claude/CLAUDE.md`와 `settings.json`은 에이전트가 읽을 정당한 이유가 있다. data-home 쪽은 `_CREW_SECRET_LEAVES`가 glob을 쓰지 않으므로 `accounts` 디렉터리를 통째로 분류한다(`profiles` 선례).

**Files:**
- Modify: `src/kiro_crew/security.py` — `_SENSITIVE_HOME_DIRS`(약 4035행), `_CREW_SECRET_LEAVES`(약 4118행)
- Test: `test/test_claude_account_secrets.py`

**Interfaces:**
- Consumes: 없음
- Produces: `is_sensitive_path()`가 새 경로들에 대해 True를 반환. 다른 모듈이 import하는 새 심볼은 없다.

- [ ] **Step 1: 실패하는 테스트를 작성**

`test/test_claude_account_secrets.py`:

```python
"""Claude credential stores must be invisible to the agent's file tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiro_crew.security import is_sensitive_path


def test_claude_credentials_file_is_sensitive(monkeypatch, tmp_path):
    """``is_sensitive_path`` is read+write by contract — one check covers both verbs,
    which matters here because a WRITABLE credentials file is a token-replacement
    vector, not just a disclosure one."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path.home() / ".claude" / ".credentials.json"

    assert is_sensitive_path(str(target)) is True


def test_claude_settings_stay_readable(monkeypatch, tmp_path):
    """Leaf-level classification, not whole-directory: settings are legitimate reads."""
    monkeypatch.setenv("HOME", str(tmp_path))

    assert is_sensitive_path(str(Path.home() / ".claude" / "settings.json")) is False
    assert is_sensitive_path(str(Path.home() / ".claude" / "CLAUDE.md")) is False


@pytest.mark.parametrize("prefix", [".kiro/crew", ".kirocrew"])
def test_account_dir_is_sensitive_under_both_data_homes(prefix, monkeypatch, tmp_path):
    """config_dir() can resolve to the legacy data home during a migration fallback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path.home() / prefix / "accounts" / "work" / ".credentials.json"

    assert is_sensitive_path(str(target)) is True


@pytest.mark.parametrize("prefix", [".kiro/crew", ".kirocrew"])
def test_account_dir_itself_is_sensitive(prefix, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert is_sensitive_path(str(Path.home() / prefix / "accounts")) is True
```

- [ ] **Step 2: 실패를 확인**

```bash
python -m pytest test/test_claude_account_secrets.py -v -n0
```

기대: credentials/accounts 케이스가 FAIL (`assert False is True`), settings 케이스는 이미 PASS.

- [ ] **Step 3: `_SENSITIVE_HOME_DIRS`에 리프를 추가**

`src/kiro_crew/security.py`, 리스트 마지막 항목(`"Library/Application Support/amazon-q",`) 뒤에 삽입:

```python
    # Claude Code's credential store. Holds the account OAuth grant (accessToken,
    # refreshToken, subscriptionType, rateLimitTier) AND a per-MCP-server bearer
    # token for every connected service, so an agent that could read it could
    # impersonate the user against all of them. A LEAF, not the whole ~/.claude:
    # CLAUDE.md and settings.json in the same directory are legitimate agent reads
    # (the .docker/config.json precedent above). Write is covered too — a writable
    # credentials file is a token-replacement vector, not just a disclosure one.
    # claude-agent-acp reads it through its own process env, never this gate, so
    # classifying it does not break the backend.
    ".claude/.credentials.json",
```

- [ ] **Step 4: `_CREW_SECRET_LEAVES`에 계정 디렉터리를 추가**

`_CREW_SECRET_LEAVES` 리스트에 삽입 (`profiles` 항목 근처):

```python
    # Account profiles are Claude Code config dirs, so each one holds its own
    # credential store. A whole DIRECTORY entry (like ``profiles``) rather than a
    # leaf: _CREW_SECRET_LEAVES joins exact ``{prefix}/{leaf}`` paths and does not
    # glob, so there is no way to name ``accounts/*/.credentials.json``. Covering
    # the directory is also the safer default — a config dir accumulates history
    # and settings sidecars that can carry the same bytes.
    "accounts",
```

- [ ] **Step 5: 통과를 확인**

```bash
python -m pytest test/test_claude_account_secrets.py -v -n0
```

기대: 8개 PASS.

- [ ] **Step 6: 보안 회귀 스위트를 확인**

```bash
python -m pytest test/ -k "sensitive or security or denied" -q
```

기대: 회귀 없음. `test_denied_commands_security.py`가 규칙 수를 고정하지만 이 태스크는 규칙을 추가하지 않으므로 영향이 없다.

- [ ] **Step 7: 게이트 후 커밋**

```bash
black src/kiro_crew/security.py test/test_claude_account_secrets.py
isort src/kiro_crew/security.py test/test_claude_account_secrets.py
flake8 src/kiro_crew/security.py test/test_claude_account_secrets.py
mypy src/kiro_crew
git add src/kiro_crew/security.py test/test_claude_account_secrets.py
git commit -m "feat: hide Claude credential stores from the agent file gate"
```

---

## Task 5: `_claude_code` 팩토리 — 실제 세션 부팅

이 태스크가 끝나면 `provider = "claude_code"`로 세션이 실제로 뜬다. `model_registry.py`의 모듈 docstring이 이미 `config.loader._claude_code`를 이름으로 참조하므로 그 이름을 그대로 쓴다.

**Files:**
- Create: `src/kiro_crew/providers/claude_code_factory.py`
- Modify: `src/kiro_crew/config/loader.py` — `create_provider_factory`(약 5172행) 진입부에 분기
- Test: `test/test_claude_code_factory.py`

**Interfaces:**
- Consumes:
  - `kiro_crew.accounts.resolve_account` / `AccountError` / `CODE_ACCOUNT_NOT_LOGGED_IN` (Task 2)
  - `kiro_crew.config.loader.PROVIDER_CLAUDE_CODE` (Task 3)
  - `kiro_crew.acp.types.ACP_BACKEND_CLAUDE`, `CC_PERMISSION_MODE_AUTO`, `CC_PERMISSION_MODE_DEFAULT`
  - `kiro_crew.providers.acp.AcpProvider`
- Produces:
  - `build_claude_code_factory(cfg: KiroCrewConfig) -> Callable[..., AcpProvider]`
  - 반환 팩토리의 호출 규약: `factory(session_key=None, *, agent=None, channel_id=None, model_override=None, cwd=None, extra_env=None, reasoning_effort_override=None, account=None, **_kwargs)`. `account`는 `SessionManager.get_or_create`의 `**extra_factory_kwargs`를 통해 전달된다(기존 시그니처 변경 불필요).

- [ ] **Step 1: 실패하는 테스트를 작성**

`test/test_claude_code_factory.py`:

```python
"""The claude_code provider factory: backend selection and account env."""

from __future__ import annotations

import json
import unittest.mock

import pytest

from kiro_crew.accounts import CODE_ACCOUNT_NOT_LOGGED_IN, AccountError
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    CC_PERMISSION_MODE_AUTO,
    CC_PERMISSION_MODE_DEFAULT,
)
from kiro_crew.config.loader import PROVIDER_CLAUDE_CODE, KiroCrewConfig
from kiro_crew.providers.claude_code_factory import build_claude_code_factory


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    (work / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "provider": PROVIDER_CLAUDE_CODE,
                    "account": "work",
                    "accounts": {"work": {"config_dir": str(work)}},
                }
            }
        )
    )
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=path):
        return KiroCrewConfig.load()


def _captured(monkeypatch):
    """Replace AcpProvider with a recorder so no process is spawned."""
    seen: dict = {}

    class _Fake:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(
        "kiro_crew.providers.claude_code_factory.AcpProvider", _Fake, raising=True
    )
    return seen


def test_factory_selects_the_claude_backend(cfg, monkeypatch):
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["acp_backend"] == ACP_BACKEND_CLAUDE


def test_factory_injects_the_account_config_dir(cfg, monkeypatch, tmp_path):
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["extra_env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "work")


def test_caller_extra_env_is_preserved(cfg, monkeypatch):
    """Account env must merge into the caller's, not replace it."""
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1", extra_env={"FOO": "bar"})

    assert seen["extra_env"]["FOO"] == "bar"
    assert "CLAUDE_CONFIG_DIR" in seen["extra_env"]


def test_account_kwarg_outranks_config(cfg, monkeypatch, tmp_path):
    """The per-session pick beats agent.account."""
    from kiro_crew.config.loader import AccountConfig

    other = tmp_path / "other"
    other.mkdir()
    (other / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "y"}}))
    cfg.agent.accounts["other"] = AccountConfig(config_dir=str(other))
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1", account="other")

    assert seen["extra_env"]["CLAUDE_CONFIG_DIR"] == str(other)


def test_not_logged_in_account_raises_with_code(cfg, monkeypatch, tmp_path):
    from kiro_crew.config.loader import AccountConfig

    empty = tmp_path / "empty"
    empty.mkdir()
    cfg.agent.accounts["empty"] = AccountConfig(config_dir=str(empty))
    _captured(monkeypatch)

    with pytest.raises(AccountError) as exc:
        build_claude_code_factory(cfg)("slot-1", account="empty")

    assert exc.value.code == CODE_ACCOUNT_NOT_LOGGED_IN


def test_auto_approval_maps_to_the_auto_permission_mode(cfg, monkeypatch):
    cfg.agent.approval_mode = "auto"
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["permission_mode"] == CC_PERMISSION_MODE_AUTO


def test_interactive_approval_maps_to_the_default_permission_mode(cfg, monkeypatch):
    """``approval_mode`` enum is ["auto", "interactive"] — interactive keeps per-tool
    approval, which KiroCrew's own PreToolUse gate still evaluates independently."""
    cfg.agent.approval_mode = "interactive"
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["permission_mode"] == CC_PERMISSION_MODE_DEFAULT


def test_model_override_is_translated_to_a_claude_provider_id(cfg, monkeypatch):
    """Canonical registry keys must not reach the backend unresolved."""
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1", model_override="auto")

    assert seen["model"] == ""


def test_create_provider_factory_dispatches_on_the_provider(cfg, monkeypatch):
    """The loader must route claude_code here instead of returning _acp."""
    seen = _captured(monkeypatch)

    cfg.create_provider_factory()("slot-1")

    assert seen["acp_backend"] == ACP_BACKEND_CLAUDE
```

- [ ] **Step 2: 실패를 확인**

```bash
python -m pytest test/test_claude_code_factory.py -v -n0
```

기대: `ModuleNotFoundError: No module named 'kiro_crew.providers.claude_code_factory'`.

- [ ] **Step 3: 팩토리를 구현**

`src/kiro_crew/providers/claude_code_factory.py`:

```python
"""Provider factory for the ``claude_code`` backend.

Drives claude-agent-acp through the SAME :class:`AcpProvider` the kiro-cli path
uses — the backend id is the only structural difference, so every consumer that
already branches on ``_is_claude_backend`` lights up without further wiring.

An account profile becomes one env var: ``CLAUDE_CONFIG_DIR``. That is what
isolates the account's credentials and history, so no credential handling lives
here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from kiro_crew import model_registry
from kiro_crew.accounts import CODE_ACCOUNT_NOT_LOGGED_IN, AccountError, resolve_account
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    CC_PERMISSION_MODE_AUTO,
    CC_PERMISSION_MODE_DEFAULT,
)
from kiro_crew.providers.acp import AcpProvider

if TYPE_CHECKING:  # circular import: config.loader -> providers
    from kiro_crew.config.loader import KiroCrewConfig

logger = logging.getLogger(__name__)

# The env var Claude Code reads its config directory from. Owning it here keeps the
# account layer free of provider-specific spelling.
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

# ``approval_mode`` value that means "do not stop for per-tool approval". The other
# enum value, ``interactive``, keeps the backend's per-tool prompt. Either way
# KiroCrew's OWN PreToolUse gate still evaluates the call — the backend's mode is
# not the security boundary.
_APPROVAL_MODE_AUTO = "auto"

# Registry index for the claude backend. Distinct from the ``acp`` index: the
# claude path downgrades ids kiro serves natively (there is no Haiku here).
_REGISTRY_PROVIDER = "claude_code"


def _permission_mode(approval_mode: str) -> str:
    """Map KiroCrew's approval mode onto the backend's permission mode."""
    if approval_mode == _APPROVAL_MODE_AUTO:
        return CC_PERMISSION_MODE_AUTO
    return CC_PERMISSION_MODE_DEFAULT


def build_claude_code_factory(cfg: KiroCrewConfig) -> Callable[..., AcpProvider]:
    """Return the provider factory for ``agent.provider == "claude_code"``.

    The returned callable mirrors the ``_acp`` factory's call signature so the
    session layer needs no branch. ``account`` arrives through
    ``SessionManager.get_or_create``'s ``**extra_factory_kwargs``, so adding it
    changes no existing signature.
    """
    default_model = cfg.agent.model
    permission_mode = _permission_mode(cfg.agent.approval_mode)
    sandbox = cfg.agent.sandbox
    tool_search = cfg.agent.tool_search

    def _claude_code(
        session_key: str | None = None,
        agent: str | None = None,
        channel_id: str | None = None,
        model_override: str | None = None,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        reasoning_effort_override: str | None = None,
        account: str | None = None,
        **_kwargs: Any,
    ) -> AcpProvider:
        resolved = resolve_account(cfg, account)
        if not resolved.logged_in:
            # Fail at session start rather than letting the adapter surface an
            # opaque auth error mid-turn: the actionable instruction is to run
            # `claude login` against THIS account's directory, and only we know
            # which directory that is.
            raise AccountError(
                CODE_ACCOUNT_NOT_LOGGED_IN,
                f"account {resolved.name!r} has no Claude login in {resolved.config_dir}",
            )

        # Merge, never replace: the caller's env carries unrelated per-session
        # values and dropping them would silently change session behavior.
        env: dict[str, str] = dict(extra_env or {})
        env[CLAUDE_CONFIG_DIR_ENV] = str(resolved.config_dir)

        model = model_override or default_model
        # Translation boundary: the wire/dropdown value is a canonical registry key
        # ("opus-4.8-1m"), which the backend does not accept. ``auto`` resolves to
        # "" — meaning "let the backend pick".
        model = model_registry.to_provider_id(model, _REGISTRY_PROVIDER) if model else ""

        return AcpProvider(
            work_dir=Path(cwd) if cwd else None,
            model=model,
            agent=agent,
            sandbox_mode=sandbox,
            session_key=session_key,
            channel_id=channel_id,
            extra_env=env,
            acp_backend=ACP_BACKEND_CLAUDE,
            tool_search=tool_search,
            permission_mode=permission_mode,
        )

    return _claude_code
```

- [ ] **Step 4: 로더에서 분기**

`src/kiro_crew/config/loader.py`의 `create_provider_factory`(약 5172행) docstring 직후, 기존 본문 앞에 삽입:

```python
        if self.agent.provider == PROVIDER_CLAUDE_CODE:
            # circular import: providers.claude_code_factory -> providers.acp -> ... -> config
            from kiro_crew.providers.claude_code_factory import build_claude_code_factory

            return build_claude_code_factory(self)
```

그리고 같은 메서드의 docstring을 현재 동작에 맞게 갱신한다(`KiroCrew는 KiroACP-only` 서술이 더 이상 참이 아니다):

```python
        """Return a factory that creates LLMProvider instances from config.

        ``agent.provider`` selects the backend: ``acp`` drives kiro-cli, and
        ``claude_code`` drives claude-agent-acp through the same AcpProvider with
        a different ``acp_backend``. The factory accepts an optional
        ``session_key`` to create a per-session subdirectory under
        ``workspace_root()``.
        """
```

- [ ] **Step 5: 통과를 확인**

```bash
python -m pytest test/test_claude_code_factory.py -v -n0
```

기대: 9개 PASS.

- [ ] **Step 6: 세션 계층 회귀를 확인**

```bash
python -m pytest test/ -k "provider or factory or session_map" -q
```

기대: 회귀 없음.

- [ ] **Step 7: 실제 부팅을 수동 확인**

`KIROCREW_HOME`을 격리해 기존 데이터 홈을 건드리지 않는다.

```bash
export KIROCREW_HOME="$(mktemp -d)/crew"
python -c "
import json, os, pathlib
home = pathlib.Path(os.environ['KIROCREW_HOME']); home.mkdir(parents=True, exist_ok=True)
(home / 'config.json').write_text(json.dumps({'agent': {'provider': 'claude_code'}}))
print(home / 'config.json')
"
kirocrew chat "reply with the single word: ok" 2>&1 | tail -20
```

기대: Claude Code 백엔드로 턴이 완료된다. `claude_acp_missing` 계열 오류가 나면 Task 1 Step 1로 돌아간다.

- [ ] **Step 8: 게이트 후 커밋**

```bash
black src/kiro_crew/providers/claude_code_factory.py src/kiro_crew/config/loader.py test/test_claude_code_factory.py
isort src/kiro_crew/providers/claude_code_factory.py src/kiro_crew/config/loader.py test/test_claude_code_factory.py
flake8 src/kiro_crew/providers/claude_code_factory.py src/kiro_crew/config/loader.py test/test_claude_code_factory.py
mypy src/kiro_crew
git add src/kiro_crew/providers/claude_code_factory.py src/kiro_crew/config/loader.py test/test_claude_code_factory.py
git commit -m "feat: build claude_code provider sessions on named accounts"
```

---

## Task 6: `GET /api/accounts`

**Files:**
- Create: `src/kiro_crew/dashboard/handlers/accounts.py`
- Modify: `src/kiro_crew/dashboard/server.py` — 라우트 등록 1줄
- Test: `test/test_accounts_endpoint.py`

**Interfaces:**
- Consumes: `kiro_crew.accounts.list_accounts` / `AccountError` (Task 2), `PROVIDER_CLAUDE_CODE` (Task 3)
- Produces:
  - `async def api_accounts_get(request: web.Request) -> web.Response`
  - 응답 바디: `{"provider": "<id>", "active": "<name>", "accounts": [{"name": str, "logged_in": bool}]}`
  - **`config_dir`, 토큰, 파일 내용은 절대 포함하지 않는다.**

- [ ] **Step 1: 실패하는 테스트를 작성**

`test/test_accounts_endpoint.py`:

```python
"""GET /api/accounts — names and login state only, never paths or tokens."""

from __future__ import annotations

import json
import unittest.mock

import pytest
from aiohttp import web

from kiro_crew.config.loader import PROVIDER_CLAUDE_CODE, KiroCrewConfig
from kiro_crew.dashboard.handlers.accounts import api_accounts_get


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    (work / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "s3cr3t"}}))
    empty = tmp_path / "empty"
    empty.mkdir()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "provider": PROVIDER_CLAUDE_CODE,
                    "account": "work",
                    "accounts": {
                        "work": {"config_dir": str(work)},
                        "empty": {"config_dir": str(empty)},
                    },
                }
            }
        )
    )
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=path):
        return KiroCrewConfig.load()


async def _call(cfg) -> dict:
    app = web.Application()
    app["config"] = cfg
    request = type("_Req", (), {"app": app})()
    response = await api_accounts_get(request)
    return json.loads(response.body.decode())


@pytest.mark.asyncio
async def test_lists_every_profile_with_login_state(tmp_path, monkeypatch):
    body = await _call(_cfg(tmp_path, monkeypatch))

    assert {a["name"]: a["logged_in"] for a in body["accounts"]} == {"work": True, "empty": False}


@pytest.mark.asyncio
async def test_reports_the_active_account_and_provider(tmp_path, monkeypatch):
    body = await _call(_cfg(tmp_path, monkeypatch))

    assert body["active"] == "work"
    assert body["provider"] == PROVIDER_CLAUDE_CODE


@pytest.mark.asyncio
async def test_never_leaks_config_dirs_or_tokens(tmp_path, monkeypatch):
    """The whole body is checked, not just the fields we remembered to assert."""
    body = await _call(_cfg(tmp_path, monkeypatch))
    raw = json.dumps(body)

    assert "s3cr3t" not in raw
    assert str(tmp_path) not in raw
    assert "config_dir" not in raw


@pytest.mark.asyncio
async def test_declaration_order_is_preserved(tmp_path, monkeypatch):
    body = await _call(_cfg(tmp_path, monkeypatch))

    assert [a["name"] for a in body["accounts"]] == ["work", "empty"]
```

- [ ] **Step 2: 실패를 확인**

```bash
python -m pytest test/test_accounts_endpoint.py -v -n0
```

기대: `ModuleNotFoundError: ...handlers.accounts`.

- [ ] **Step 3: 핸들러를 구현**

`src/kiro_crew/dashboard/handlers/accounts.py`:

```python
"""Dashboard endpoint for Claude account profiles.

Deliberately read-only and name-only: adding or authenticating an account is a
``claude login`` in a terminal, not something the dashboard should broker. The
response NEVER carries a config dir or credential bytes — the dropdown only needs
a name and whether it can start a session.
"""

from __future__ import annotations

import logging

from aiohttp import web

from kiro_crew.accounts import list_accounts, resolve_account

logger = logging.getLogger(__name__)


async def api_accounts_get(request: web.Request) -> web.Response:
    """GET /api/accounts — declared account profiles and their login state."""
    cfg = request.app["config"]
    try:
        active = resolve_account(cfg).name
    except Exception:
        # A misconfigured active account must not blank the whole list: the user
        # needs to SEE the profiles in order to pick a working one.
        logger.warning("active account did not resolve; listing anyway", exc_info=True)
        active = ""
    return web.json_response(
        {
            "provider": cfg.agent.provider,
            "active": active,
            "accounts": [
                {"name": a.name, "logged_in": a.logged_in} for a in list_accounts(cfg)
            ],
        }
    )
```

- [ ] **Step 4: 라우트를 등록**

`src/kiro_crew/dashboard/server.py`의 다른 `app.router.add_get("/api/...")` 등록 근처(예: 약 901행 `"/api/browser/config"` 블록)에 추가:

```python
    app.router.add_get("/api/accounts", accounts_handlers.api_accounts_get)
```

필요한 import를 같은 파일의 핸들러 import 블록에 추가한다:

```python
from kiro_crew.dashboard.handlers import accounts as accounts_handlers
```

- [ ] **Step 5: 통과를 확인**

```bash
python -m pytest test/test_accounts_endpoint.py -v -n0
```

기대: 4개 PASS.

- [ ] **Step 6: 라우트 등록을 실제로 확인**

```bash
python -c "
from kiro_crew.dashboard.server import create_app
import asyncio
app = asyncio.get_event_loop().run_until_complete(create_app()) if False else None
print('skip live app; grep instead')
"
grep -n '/api/accounts' src/kiro_crew/dashboard/server.py
```

기대: 등록 줄이 정확히 하나 출력된다. (앱 생성은 부팅 의존성이 많아 grep으로 확인하고, 실제 호출은 Task 7의 브라우저 확인에서 검증한다.)

- [ ] **Step 7: 게이트 후 커밋**

```bash
black src/kiro_crew/dashboard/handlers/accounts.py src/kiro_crew/dashboard/server.py test/test_accounts_endpoint.py
isort src/kiro_crew/dashboard/handlers/accounts.py src/kiro_crew/dashboard/server.py test/test_accounts_endpoint.py
flake8 src/kiro_crew/dashboard/handlers/accounts.py src/kiro_crew/dashboard/server.py test/test_accounts_endpoint.py
mypy src/kiro_crew
git add src/kiro_crew/dashboard/handlers/accounts.py src/kiro_crew/dashboard/server.py test/test_accounts_endpoint.py
git commit -m "feat: expose account profiles over the dashboard API"
```

---

## Task 7: 대시보드 계정 드롭다운 (i18n 포함)

계정은 세션 시작 시점에 고정된다. 따라서 드롭다운은 **슬롯에 라이브 세션이 없을 때만 활성**이고, 세션이 뜬 뒤에는 잠긴다. 이렇게 하면 "전환 = 새 세션"이라는 스펙의 의미가 UI에서 그대로 읽히고, 새로운 중간 상태(재시작 필요 배너 등)를 도입하지 않는다.

**Files:**
- Create: `website/src/components/AccountDropdown.tsx`
- Create: `website/src/test/AccountDropdown.test.tsx`
- Modify: `website/src/api/client.ts` — `accounts` 메서드
- Modify: `website/src/providers/types.ts` — `ProviderCapabilities.accountProfiles`
- Modify: `website/src/providers/adapters/acp.ts` — 위 capability 값
- Modify: `website/src/pages/ChatPage.tsx` — 렌더 지점 (약 5286행 `ModelEffortDropdown` 인접)
- Modify: `website/src/i18n/locales/*.json` — 생성/번역 파이프라인 경유

**Interfaces:**
- Consumes: `GET /api/accounts` (Task 6)
- Produces:
  - `api.accounts(): Promise<AccountsResponse>` — `{ provider: string; active: string; accounts: { name: string; logged_in: boolean }[] }`
  - `export default function AccountDropdown(props: { slot: string; disabled: boolean; onSelect: (name: string) => void })`
  - `ProviderCapabilities.accountProfiles: boolean`

- [ ] **Step 1: API 클라이언트 메서드를 추가**

`website/src/api/client.ts`의 `effortLevels`(약 1004행) 근처에 추가:

```ts
  accounts: () =>
    fetch('/api/accounts').then(j) as Promise<{
      provider: string
      active: string
      accounts: { name: string; logged_in: boolean }[]
    }>,
```

- [ ] **Step 2: capability 필드를 추가**

`website/src/providers/types.ts`의 `ProviderCapabilities`에 추가:

```ts
  accountProfiles: boolean
```

`website/src/providers/adapters/acp.ts`의 capabilities 리터럴에 추가 — kiro-cli 경로는 계정 프로필을 갖지 않으므로 `false`:

```ts
  accountProfiles: false,
```

- [ ] **Step 3: 실패하는 테스트를 작성**

`website/src/test/AccountDropdown.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AccountDropdown from '../components/AccountDropdown'

vi.mock('../api/client', () => ({
  api: {
    accounts: vi.fn(() =>
      Promise.resolve({
        provider: 'claude_code',
        active: 'work',
        accounts: [
          { name: 'work', logged_in: true },
          { name: 'personal', logged_in: false },
        ],
      })
    ),
  },
}))

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('AccountDropdown', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists every profile returned by the API', async () => {
    wrap(<AccountDropdown slot="s1" disabled={false} onSelect={() => {}} />)
    await userEvent.click(await screen.findByRole('button'))

    expect(await screen.findByText('work')).toBeInTheDocument()
    expect(screen.getByText('personal')).toBeInTheDocument()
  })

  it('reports the selected account to its parent', async () => {
    const onSelect = vi.fn()
    wrap(<AccountDropdown slot="s1" disabled={false} onSelect={onSelect} />)
    await userEvent.click(await screen.findByRole('button'))
    await userEvent.click(await screen.findByText('personal'))

    expect(onSelect).toHaveBeenCalledWith('personal')
  })

  it('does not open while disabled', async () => {
    wrap(<AccountDropdown slot="s1" disabled onSelect={() => {}} />)
    await userEvent.click(await screen.findByRole('button'))

    await waitFor(() => expect(screen.queryByText('personal')).not.toBeInTheDocument())
  })

  it('marks a profile with no login as unavailable', async () => {
    wrap(<AccountDropdown slot="s1" disabled={false} onSelect={() => {}} />)
    await userEvent.click(await screen.findByRole('button'))

    const row = (await screen.findByText('personal')).closest('button')
    expect(row).toBeDisabled()
  })
})
```

- [ ] **Step 4: 실패를 확인**

```bash
cd website && npx vitest run src/test/AccountDropdown.test.tsx
```

기대: `Failed to resolve import "../components/AccountDropdown"`.

- [ ] **Step 5: 컴포넌트를 구현**

`website/src/components/AccountDropdown.tsx`. 문자열은 영어 리터럴로 쓰고, Step 8의 codemod가 카탈로그로 추출한다.

```tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import { UserRound, Check } from 'lucide-react'
import { api } from '../api/client'

interface Props {
  slot: string
  /** True once the slot has a live session: the account is fixed at session
   *  start, so switching means starting a new session, not mutating this one. */
  disabled: boolean
  onSelect: (name: string) => void
}

/** Account picker for the claude_code provider. Reads the declared profiles from
 *  /api/accounts (names and login state only — the API never returns config dirs)
 *  and locks itself once the slot has a session. */
export default function AccountDropdown({ slot, disabled, onSelect }: Props) {
  const [open, setOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ['accounts', slot],
    queryFn: () => api.accounts(),
    staleTime: 0,
  })

  const accounts = data?.accounts ?? []
  const active = data?.active ?? ''

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen(!open)}
        title={disabled ? 'Start a new session to switch account' : 'Switch account'}
      >
        <UserRound className="lucide-inline" />
        {active}
      </button>

      <AnimatePresence>
        {open && !disabled && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
          >
            {accounts.map(a => (
              <button
                key={a.name}
                type="button"
                disabled={!a.logged_in}
                title={a.logged_in ? '' : 'Run claude login for this account first'}
                onClick={() => {
                  onSelect(a.name)
                  setOpen(false)
                }}
              >
                {a.name === active && <Check className="lucide-inline" />}
                {a.name}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
```

- [ ] **Step 6: 통과를 확인**

```bash
cd website && npx vitest run src/test/AccountDropdown.test.tsx
```

기대: 4개 PASS.

- [ ] **Step 7: `ChatPage`에 배선**

`website/src/pages/ChatPage.tsx`:

1. import를 추가 (약 75행 `ModelEffortDropdown` import 근처):

```tsx
import AccountDropdown from '../components/AccountDropdown'
```

2. `ModelEffortDropdown` 렌더 지점(약 5286행) 인접에 배치. capability로 게이트하고, 라이브 세션 여부로 `disabled`를 준다:

```tsx
{provider.capabilities.accountProfiles && (
  <AccountDropdown
    slot={activeSlot}
    disabled={Boolean(currentSlot?.session_id)}
    onSelect={name => setPendingAccount(name)}
  />
)}
```

3. `pendingAccount` 상태를 `reasoningEffortDropdown` 상태 선언부(약 967행) 근처에 추가하고, 세션을 시작하는 요청에 `account: pendingAccount`를 실어 보낸다. 채팅 전송 경로가 세션 생성 파라미터를 만드는 지점에 필드를 추가한다 — 백엔드는 이 값을 `SessionManager.get_or_create`의 `**extra_factory_kwargs`로 흘려 `_claude_code` 팩토리의 `account` 인자에 도달시킨다.

```tsx
  const [pendingAccount, setPendingAccount] = useState('')
```

- [ ] **Step 8: 카탈로그를 생성하고 10개 언어로 번역**

`en.json`은 **생성물이다 — 절대 손으로 편집하지 않는다.**

```bash
cd website
node scripts/i18n-codemod.mjs
git diff --stat src/i18n/locales/en.json
```

기대: 새 컴포넌트의 문자열만 추가된다.

번역 파이프라인(오프라인):

```bash
SHARDS="$(mktemp -d)/i18n-shards"
node scripts/i18n-shard.mjs split "$SHARDS"
node scripts/i18n-translate.mjs emit "$SHARDS"
# 각 (locale, shard) 프롬프트를 처리하고 답변을 채운 뒤:
node scripts/i18n-translate.mjs verify "$SHARDS" --locale zh-CN
node scripts/i18n-translate.mjs merge "$SHARDS"
```

주의:
- 샤드 디렉터리는 **워크트리 밖**에 둔다 (dirty tree가 worktree pruning을 막는다)
- `i18n-shard.mjs join`을 쓰지 않는다 — CLDR 복수형(`_few`/`_many`)을 조용히 버린다. 반드시 `i18n-translate.mjs merge`
- 짧거나 모호한 문자열은 `src/i18n/en.context.json`에 컨텍스트를 추가한다
- 손으로 카탈로그를 조립하지 않는다 — `merge`의 fail-closed 검사가 영어 텍스트가 번역으로 위장해 출하되는 것을 막는 유일한 장치다

- [ ] **Step 9: 프론트엔드 전체 게이트**

```bash
cd website
npx tsc -b            # npm run typecheck 는 0개 파일을 검사한다 — 쓰지 않는다
npm run test          # pretest 로 jscpd 중복 검사가 먼저 돌아간다
npm run build
```

기대: 전부 통과. i18n 패리티 테스트(`catalogParity`, `deadKeys`, `englishIdentity`)가 10개 로케일의 키 집합 일치를 검증한다.

- [ ] **Step 10: 빌드 산출물을 패키지로 스테이징하고 실물 확인**

```bash
cd /Users/soon/Projects/Code/KiroCrew
rm -rf src/kiro_crew/static/dist
mkdir -p src/kiro_crew/static
cp -R website/dist src/kiro_crew/static/dist
export KIROCREW_HOME="$(mktemp -d)/crew"
kirocrew gateway
```

브라우저에서 대시보드를 열어 확인한다: 계정 드롭다운이 보이고, 프로필이 나열되고, 미로그인 프로필이 비활성이고, 세션을 시작한 뒤 드롭다운이 잠긴다.

- [ ] **Step 11: 커밋**

빌드 산출물은 커밋하지 않는다 — `src/kiro_crew/static/dist`와 `website/dist`는 둘 다 gitignore 대상이고(`.gitignore:55`, `website/.gitignore:2`), Vite가 콘텐츠 해시 파일명을 뿜기 때문에 의도적으로 추적하지 않는다. 패키징 시점에 `setup.py`의 `BuildWithFrontend`가 스테이징한다. Step 10의 복사는 로컬 실물 확인용일 뿐이다.

```bash
git add website/src
git commit -m "feat: pick a Claude account from the dashboard before a session starts"
```

---

## Task 8: 포크 divergence 문서화 + 전체 게이트

**Files:**
- Modify: `AGENTS.md` — "Never re-introduce" 절의 "Other providers" 항목
- Modify: `docs/system-specs/modules/providers.md`
- Test: 전체 스위트

**Interfaces:**
- Consumes: Task 2~7의 전체 구현
- Produces: 없음 (문서 + 게이트)

- [ ] **Step 1: `AGENTS.md`에 포크 분기를 기록**

"Never re-introduce" 절의 `**Other providers.**` 항목에 이 포크의 의도적 분기를 명시한다. 항목을 삭제하지 말고, 분기 사실과 이유를 덧붙인다 — upstream sync 때 왜 충돌하는지가 남아야 한다:

```markdown
- **Other providers.** Upstream Kiro Crew is KiroACP-only. **This fork deliberately
  diverges:** `agent.provider` also accepts `claude_code`, which drives
  claude-agent-acp through the `ACP_BACKEND_CLAUDE` seam, with named account
  profiles bound to `CLAUDE_CONFIG_DIR`. The logic lives in
  `providers/claude_code_factory.py` + `accounts.py`; the core edits are the
  provider enum and factory dispatch in `config/loader.py`, two credential leaves
  in `security.py`, and one dashboard route. An upstream sync must re-apply those
  hunks rather than reverting them. Design:
  [claude-code-accounts](docs/task-specs/2026/08/claude-code-accounts/design.md).
  Keep the dormant `_is_claude` seam intact; do NOT add a third provider.
```

- [ ] **Step 2: 소유 스펙을 갱신**

`docs/system-specs/modules/providers.md`에 `claude_code` 프로바이더와 계정 프로필을 문서화한다. 시스템 스펙은 **현재 동작**을 서술하는 문서이므로(task spec과 달리 계속 갱신된다), 다음을 담는다: provider 값, `agent.account`/`agent.accounts` 스키마, `CLAUDE_CONFIG_DIR` 격리, 워커 풀 우회 한계, `GET /api/accounts` 계약, 에러 코드 3개.

- [ ] **Step 3: docs 게이트**

```bash
bash scripts/docs-lint.sh
```

기대: 통과. 새 문서를 만들지 않았으므로 인덱스 갱신은 불필요하다.

- [ ] **Step 4: 스크럽·브랜드 게이트**

```bash
bash scripts/scrub-lint.sh
BRAND_BASE_REF=upstream/main python3 scripts/check_brand_name.py
```

기대: 둘 다 통과.

- [ ] **Step 5: 백엔드 전체 스위트**

```bash
black src/kiro_crew test && isort src/kiro_crew test
flake8 src/kiro_crew test && mypy src/kiro_crew
python -m pytest
```

기대: 전부 통과. 실패한 테스트는 재실행·`sleep` 연장·단정 약화로 넘기지 않는다 — [testing-conventions](../../../../system-specs/common/testing-conventions.md) § Determinism의 5개 flake 유형과 각각의 유일한 올바른 수정을 읽는다.

- [ ] **Step 6: 프론트엔드 전체 스위트**

```bash
cd website && npx tsc -b && npm run test && npm run build
```

기대: 전부 통과.

- [ ] **Step 7: 커밋**

```bash
git add AGENTS.md docs/system-specs/modules/providers.md
git commit -m "docs: record the claude_code provider fork divergence"
```

---

## 자기 점검 결과

**스펙 커버리지**

| 스펙 요구 | 태스크 |
|---|---|
| `_claude_code` 팩토리 | 5 |
| provider enum 확장 | 3 |
| 계정 프로필 레이어 | 2, 3 |
| `~/.claude` 기본 계정으로 무설정 동작 | 2 (`test_no_accounts_block_resolves_implicit_default`) |
| credentials 리프 차단 (읽기+쓰기, 양쪽 data-home) | 4 |
| `GET /api/accounts`가 토큰·경로 미노출 | 6 (`test_never_leaks_config_dirs_or_tokens`) |
| 대시보드 드롭다운 | 7 |
| 에러 코드 3개 | 2 (`account_unknown`, `account_not_logged_in`), 5 (미로그인 발생 지점), Task 5 Step 7 (`claude_acp_missing` 확인) |
| 워커 풀 우회를 한계로 문서화 | 8 Step 2 |
| 선행 스파이크 | 1 |
| upstream divergence 기록 | 8 Step 1 |

**스펙 대비 정제:** `platform/defaults.py` 편집 불필요(위 "스펙과의 차이"), `_CREW_SECRET_LEAVES`는 glob 미지원이므로 `accounts` 디렉터리 단위 분류.

**미해결 의존:** Task 7 Step 7의 3번 항목은 `ChatPage.tsx`가 세션 생성 파라미터를 조립하는 정확한 지점을 구현자가 찾아야 한다. 파일이 5,300행을 넘어 계획 작성 시점에 단일 앵커를 특정하지 못했다. `activeSlot`으로 세션을 시작하는 호출을 찾아 `account` 필드를 추가한다.
