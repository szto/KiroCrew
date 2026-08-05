# Claude Code 백엔드 + 멀티 계정 프로필 (Phase 1)

작성일 2026-08-05. 이 문서는 작성 시점의 의도를 기록한 아카이브다.

## 목표

`szto/KiroCrew` 포크에서 kiro-cli 대신 **Claude Code를 ACP 백엔드로 쓰고**, 여러
Claude 계정을 **이름 붙은 프로필로 등록해 세션 시작 시 선택**할 수 있게 한다.

## 비목표 (별도 Phase)

- **Codex 백엔드** — Phase 1.5. Codex는 ACP를 말하지 않고 `codex app-server`
  (JSON-RPC, `[experimental]`)를 별도 `LLMProvider`로 구현해야 한다. 자체 스펙을
  갖는다.
- **자동 페일오버** — Phase 2. 레이트리밋에 걸린 계정을 쿨다운시키고 다음 계정으로
  턴을 이어가는 로직. Phase 1의 프로필 추상화 위에 올린다.
- **계정별 워커 풀** — "알려진 한계" 참조.

## 배경: 왜 이 작업이 작은가

이 저장소는 내부 패키지의 공개 포크이고, `AGENTS.md`는 Claude 백엔드 등록 글루를
의도적으로 제거한 채 **휴면 seam은 보존**했다. 조사 결과 제거된 것은 팩토리와 config
enum 뿐이며, 소비하는 쪽 코드는 살아 있다.

작성 시점에 이미 존재하는 것:

| 자산 | 위치 |
|---|---|
| `acp_backend` / `extra_env` / `permission_mode` 생성자 인자 | `providers/acp.py` `AcpProvider.__init__` |
| `claude-agent-acp` spawn, `CLAUDE_CODE_EXECUTABLE` 해석(mise → augmented PATH), 세션별 `settings.local.json` 권한 모드 주입 | `acp/client.py` |
| `ACP_BACKEND_CLAUDE`, `CC_PERMISSION_MODE_DEFAULT/AUTO` | `acp/types.py` |
| 엔타이틀먼트 반영 모델 목록 (`_cc_models`, `_advertised_cc_models`), `cc_model`, `cc_commands` | `dashboard/handlers/agents.py` |
| `claude_code` 분기 | `session.py`, `dashboard/chat_runner.py`, `model_registry.py`, `context.py` 등 (작성 시점 25개 파일) |
| 등록 seam | `platform/interfaces.py` `ProviderRegistry`, `platform/defaults.py` `DefaultProviderRegistry` |

없는 것 = 이 작업의 범위:

1. `_claude_code` 프로바이더 팩토리
2. `register_acp_backends()`의 실제 등록
3. `agent.provider` enum에 `claude_code` 추가
4. **계정 프로필 레이어** (신규 개념)
5. `/api/accounts` + 대시보드 드롭다운

## 아키텍처

### 격리 전략

포크 로직은 **신규 파일**에 담고, 코어 편집은 위임 훅으로 최소화한다. upstream 동기화
시 충돌 표면을 작은 hunk 두 개로 묶는 것이 목적이다.

```
신규 파일 (upstream에 없음 → 충돌 없음)
  src/kiro_crew/accounts.py                       계정 프로필 해석
  src/kiro_crew/providers/claude_code_factory.py  _claude_code 팩토리

코어 편집 (작고 국소적인 hunk 3개)
  src/kiro_crew/config/loader.py       provider enum 확장 + account/accounts 필드
  src/kiro_crew/platform/defaults.py   DefaultProviderRegistry → 신규 모듈 위임
  src/kiro_crew/security.py            credentials 리프 추가
```

대시보드 변경(핸들러 + React + i18n 카탈로그)은 이 목록과 별개이며 "API와
프론트엔드" 절이 다룬다.

`create_factory(cfg)`와 `register_acp_backends()`는 `ProviderRegistry` 프로토콜이
정의한 정식 확장점이다. 위임 형태를 유지하면 upstream이 `defaults.py`를 리팩터해도
충돌이 국소적이다.

### 채택하지 않은 대안

**plugin entry point (`kirocrew.plugins`).** 코어 편집이 거의 0이라 매력적이지만
`platform/discovery.py`의 대가가 크다: `KIROCREW_PROFILE != standalone`이 필요하고,
`PlatformContext` 전체를 합성해야 하며, `AdmissionPolicy` 서명/allowlist 게이트와
`assert_policy_signature_satisfied` + `assert_profiles_within_ceiling`을 통과해야
한다. 모두 fail-closed다. 개인 포크에 맞지 않는 의식이므로 배제한다.

**프론트엔드 `KIROCREW_EDITION_DIR` seam.** 별도 에디션 저장소가 필요하다. 같은
이유로 배제하고, 프론트엔드는 코어 파일을 직접 수정한다.

### 데이터 흐름

```
config.json  agent.provider = "claude_code"
             agent.account  = "work"
   │
   ├─ accounts.py       계정명 → config_dir 해석, 존재/로그인 검증
   │
   └─ claude_code_factory.py
        AcpProvider(acp_backend=ACP_BACKEND_CLAUDE,
                    extra_env={"CLAUDE_CONFIG_DIR": <dir>},   # 기본 dir이면 생략
                    permission_mode=CC_PERMISSION_MODE_*,
                    model=<cc_model 해석>)
              │
              └─ acp/client.py  (기존 경로 변경 없음)
                    └─ claude-agent-acp
                         env CLAUDE_CODE_EXECUTABLE=<resolved claude>
```

## 데이터 모델

계정은 새 인증 체계가 아니라 **Claude Code config 디렉터리에 붙인 이름**이다.
Claude Code는 `CLAUDE_CONFIG_DIR`로 상태를 격리하므로 그것을 그대로 활용한다.

단, `CLAUDE_CONFIG_DIR`을 **기본 디렉터리(`~/.claude`)로 지정하는 것은 변수를
설정하지 않는 것과 동등하지 않다.** 변수가 설정되면 Claude Code는 상태 파일을
`$CLAUDE_CONFIG_DIR/.claude.json`에서 읽지만, 기본 레이아웃은 그 파일을
`~/.claude.json`에 두고 `~/.claude`에는 credential만 둔다. 그래서 기본 디렉터리를
명시하면 세션이 `configuration file not found`로 부팅된다. 기본 디렉터리로 해석된
계정은 변수를 **주입하지 않고** Claude Code 자신의 해석을 물려받아야 한다
(`ResolvedAccount.config_dir_env` → `None`). 빈 문자열 여부가 아니라 해석된 경로로
판정하므로 `config_dir: "~/.claude"`라고 적은 프로필도 함께 걸러진다.

```json
"agent": {
  "provider": "claude_code",
  "account": "personal",
  "accounts": {
    "personal": { "config_dir": "~/.claude" },
    "work":     { "config_dir": "~/.kiro/crew/accounts/work" }
  }
}
```

기본 계정이 기존 `~/.claude`를 가리키는 것이 설계 의도다. **현재 로그인된 계정으로
설정 없이 즉시 동작**하고, 추가 계정만 새 디렉터리에 `claude login`한다.

`accounts`가 비어 있으면 단일 암묵 계정(`~/.claude`)으로 동작해, 멀티 계정을 쓰지
않는 사용자에게 개념이 노출되지 않는다.

## 보안

### credentials 리프를 sensitive-path에 추가한다

`security.py`의 `_SENSITIVE_HOME_DIRS`는 이미 `.aws`, `.ssh`, `.gnupg`, `.netrc`,
`.midway`, `.local/share/kiro-cli` 등 **모든 credential 저장소를 에이전트 파일
툴로부터 차단**한다. Claude의 credential 파일은 upstream에 존재하지 않아 목록에
없었을 뿐이므로, 추가는 기존 원칙의 확장이며 완화가 아니다.

`~/.claude/.credentials.json`이 실제로 담는 것(작성 시점 Claude Code 2.1.222):

- `claudeAiOauth`: `accessToken`, `refreshToken`, `subscriptionType`, `rateLimitTier`
- `mcpOAuth`: 연결된 MCP 서버별 `accessToken` / `clientSecret` / `refreshToken`

즉 계정 토큰과 연결된 SaaS 베어러 토큰 다발이다. 에이전트가 읽을 수 있으면 사용자를
그 모든 서비스에 대해 사칭할 수 있다.

**디렉터리 전체가 아니라 리프만 분류한다.** `~/.claude/CLAUDE.md`와
`settings.json`은 에이전트가 읽을 정당한 이유가 있다. `.docker/config.json`,
`.kube/config`가 이미 리프 단위인 선례를 따른다.

```
.claude/.credentials.json    _SENSITIVE_HOME_DIRS   리프 (.docker/config.json 선례)
accounts                     _CREW_SECRET_LEAVES    디렉터리 (profiles 선례)
```

`_CREW_SECRET_LEAVES`는 `{prefix}/{leaf}`로 결합되는 **정확 경로 리프만** 받고 glob을
쓰지 않는다. 따라서 data-home 쪽은 `accounts` 디렉터리 전체를 분류한다 — 이미
디렉터리 엔트리인 `profiles`와 같은 형태이며, `_CREW_HOME_PREFIXES` 덕분에
`.kiro/crew`와 레거시 `.kirocrew` 양쪽이 커버된다. 계정 디렉터리는 credential을 담는
Claude Code config dir이므로 디렉터리 단위 분류가 오히려 안전하다(`.midway`가 같은
이유로 디렉터리 단위다).

`.local/share/kiro-cli` 항목의 주석이 명시하듯 **sandbox bind-mount 목록은 별개**다.
따라서 이 분류는 `claude-agent-acp` 자신의 인증을 깨지 않고, 차단 대상은 에이전트의
파일 툴로 한정된다.

### API가 노출하지 않는 것

`GET /api/accounts`는 **계정 이름과 로그인 여부만** 반환한다. 토큰, `config_dir`
절대경로, 파일 내용은 응답에 절대 포함하지 않는다.

## API와 프론트엔드

- `GET /api/accounts` → `[{ "name": "personal", "logged_in": true }, ...]`
- 기존 세션 생성 경로가 계정명을 함께 받아 슬롯에 저장한다. 세션은 시작 시점의 계정에
  고정되고, 이후 config의 `agent.account`가 바뀌어도 진행 중인 세션은 영향받지 않는다.
  계정 전환은 새 세션을 뜻한다.
- 드롭다운은 `website/src/components/ModelEffortDropdown.tsx` 패턴을 따른다.
- 백엔드가 여전히 ACP이므로 `website/src/providers/adapters/acp.ts`를 재사용한다.
  **신규 프론트엔드 어댑터는 만들지 않는다.**
- 아이콘은 `lucide-react` + `className="lucide-inline"`만 사용한다(이모지·직접 SVG
  금지, `AUTOSDE.yaml`이 강제).

## 에러 처리

백엔드 소유 문자열은 i18n 카탈로그 경로가 없으므로, `AGENTS.md`대로 **모든 non-2xx
JSON 바디에 기계 판독용 `code`를 싣는다**.

| `code` | 상황 | 사용자에게 필요한 행동 |
|---|---|---|
| `account_unknown` | `agent.account`가 `accounts`에 없음 | 이름 확인 또는 프로필 추가 |
| `account_not_logged_in` | config_dir에 유효한 credentials 없음 | 해당 dir로 `claude login` |
| `claude_acp_missing` | `claude-agent-acp` 미설치 | `npm i -g @agentclientprotocol/claude-agent-acp` |

`claude_acp_missing`은 `acp/client.py`의 기존 바이너리 부재 경로를 재사용한다.

## 알려진 한계

**워커 풀이 우회된다.** `session.py`는 `extra_env`가 있으면
`pool_decision = "bypass_env"`로 warm provider 풀을 건너뛴다. 계정 프로필은
`CLAUDE_CONFIG_DIR`을 `extra_env`로 전달하므로 **모든 세션이 콜드 스타트**가 된다.

Phase 1은 이를 수용한다. 풀 키에 계정을 포함해 계정별 풀을 두는 것이 올바른 해법이지만
`session.py` 코어 수술이므로, 동작을 먼저 확인한 뒤 별도로 판단한다.

## 선행 스파이크 (구현 착수 전 필수)

**Claude credential 격리가 실제로 `CLAUDE_CONFIG_DIR`을 따르는가?**

작성 시점 이 머신에는 credential이 **두 곳**에 있다:

- `~/.claude/.credentials.json` (mode 600, 유효한 `claudeAiOauth` 포함)
- macOS Keychain 항목 `Claude Code-credentials`

`CLAUDE_CONFIG_DIR`을 다른 디렉터리로 지정했을 때 credential도 따라가면 설계가
성립한다. Keychain이 이겨서 두 계정이 같은 항목을 공유하면 **멀티 계정이 무너진다.**

- 검증: 빈 디렉터리를 `CLAUDE_CONFIG_DIR`로 주고 인증 상태가 미로그인으로 보이는지 확인
- Keychain이 이길 경우의 대안: 계정별 API 키/토큰 주입(`ANTHROPIC_API_KEY` 등)으로
  프로필 의미를 재정의

이 스파이크 결과가 Phase 1의 성립 조건이다.

**전제조건:** `claude-agent-acp`는 현재 미설치다.
`npm i -g @agentclientprotocol/claude-agent-acp`가 필요하다. 어댑터는 native 바이너리
패키지를 누락할 수 있으므로, `acp/client.py`가 이미 하는 대로
`CLAUDE_CODE_EXECUTABLE`로 기존 `claude` 설치를 가리켜 해결한다.

## 테스트와 게이트

### 백엔드

```bash
black src/kiro_crew test && isort src/kiro_crew test
flake8 src/kiro_crew test && mypy src/kiro_crew
python -m pytest
```

신규 테스트:

- 계정 해석: 알려진 이름, 미지의 이름, `accounts` 미설정 시 암묵 기본값
- 팩토리: `acp_backend`와 `CLAUDE_CONFIG_DIR`이 `AcpProvider`에 실제로 전달되는지
- sensitive-path: 새 credential 리프가 읽기 **및 쓰기** 모두 차단되는지
- `/api/accounts`가 토큰이나 절대경로를 노출하지 않는지
- 에러 바디가 표의 `code` 값을 싣는지

`asyncio: mode=strict`이므로 async 테스트마다 `@pytest.mark.asyncio`가 필요하다.

### 프론트엔드

```bash
cd website && npx tsc -b && npm run test && npm run build
```

함정:

- **`npm run typecheck`는 0개 파일을 검사한다** (루트 `tsconfig.json`의
  `"files": []` + project references). 타입 체크는 반드시 `npx tsc -b`.
- 신규 i18n 키는 **10개 로케일 전부** 채워야 한다. `catalogParity.test.ts`,
  `deadKeys.test.ts`, `englishIdentity.test.ts`가 잡는다.
- `npm test`는 `pretest`로 jscpd 중복 검사를 먼저 돌려, 복붙만으로도 테스트 실행
  전에 실패한다.
- 날짜·숫자·정렬은 `src/i18n/format.ts` seam을 경유하고 로케일을 명시한다.

### 포크 게이트

```bash
bash scripts/scrub-lint.sh
BRAND_BASE_REF=origin/main python3 scripts/check_brand_name.py
bash scripts/docs-lint.sh
```

`website/AUTOSDE.yaml`은 live authoritative이며 `blocking: true` 규칙이 리뷰어
프롬프트를 이긴다. 프론트엔드 변경 전에 읽는다.

## upstream 동기화 메모

이 작업은 `AGENTS.md`의 "Other providers" 불변식(`agent.provider`는 `acp` 고정,
공개 등록 글루 금지)을 **의도적으로 위반하는 포크 분기**다. 개인 포크의 명시적 요구
사항이므로 수용하되, 다음을 지킨다:

- 로직은 신규 파일에 두고 코어 편집은 위임 훅으로 최소화
- `AGENTS.md`에 이 분기를 포크 divergence로 기록해 이후 sync 때 의도가 남게 한다
- upstream이 `defaults.py` / `loader.py`를 리팩터하면 두 hunk만 재적용
