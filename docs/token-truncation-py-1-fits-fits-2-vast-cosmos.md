# Plan: Refactor token_truncation.py

## Context

`token_truncation.py` has several issues: two overlapping `fits`/`_fits` functions with a bug in `_fits` (checks total tokens instead of per-role), strategies B–F operate on all units instead of only "middle" units, strategy E's binary search range is artificially capped at 10%, strategy F uses a hardcoded 30% slice instead of a configurable ratio, and there are no unit tests. This plan fixes all 8 requirements.

---

## Files to Modify

- `src/context/truncation/token_truncation.py` — main refactor
- `config/config.json` — add 3 new config keys
- `tests/unit/test_token_truncation.py` — create (new file)

---

## Step 1 — config.json

Add three keys inside `context_truncation.react`:

```json
"keep_first_units": 1,
"keep_last_units": 3,
"summary_ratio": 0.20
```

---

## Step 2 — `ReActTruncationConfig` + `TruncatorFactory`

Add three fields to the frozen dataclass (with defaults so existing code is unaffected):

```python
keep_first_units: int = 1
keep_last_units: int = 3
summary_ratio: float = 0.20
```

Extend `TruncatorFactory.create()` to read them from config:

```python
keep_first_units=int(config.get("context_truncation.react.keep_first_units", 1)) if config else 1,
keep_last_units=int(config.get("context_truncation.react.keep_last_units", 3)) if config else 3,
summary_ratio=float(config.get("context_truncation.react.summary_ratio", 0.20)) if config else 0.20,
```

---

## Step 3 — Add `_get_middle_units` helper

```python
def _get_middle_units(self, units: list[ReasoningUnit]) -> tuple[list[ReasoningUnit], list[ReasoningUnit], list[ReasoningUnit]]:
    kf = self._cfg.keep_first_units
    kl = self._cfg.keep_last_units
    head = units[:kf]
    tail = units[len(units) - kl:] if kl > 0 and len(units) > kl else []
    end_idx = len(units) - kl if kl > 0 else len(units)
    middle = units[kf:end_idx]
    return head, middle, tail
```

When `len(units) <= kf + kl`, `middle` will be empty `[]`.

---

## Step 4 — Unify `fits` / remove `_fits`

Replace the `fits(m, role)` closure in `truncate()` with a no-argument-for-role version that always checks only assistant+tool per-role budgets:

```python
def fits(m: list[LLMMessage]) -> bool:
    est = effective_estimator.estimate(LLMRequest(messages=m), ["assistant", "tool"])
    return (
        est["assistant"] <= budget.role_budgets["assistant"].token_budget
        and est["tool"] <= budget.role_budgets["tool"].token_budget
    )
```

- Remove `_fits()` method entirely.
- Update all `fits(msgs, [...])` call sites → `fits(msgs)`.
- Update `_strategy_e_binary_drop` signature: remove `budget` and `estimator` params, add `fits: Callable[[list[LLMMessage]], bool]`.
- Update call site: `self._strategy_e_binary_drop(msgs, fits)`.
- The initial budget check in `truncate()` also uses `fits` — update accordingly.

---

## Step 5–7 — Update Strategies B, C, D

All three now operate only on middle units. Pattern:

```python
units = _parse_reasoning_units(messages)
_, middle_units, _ = self._get_middle_units(units)
if not middle_units:
    return messages
middle_ids = {id(m) for u in middle_units for m in _unit_to_messages(u)}
```

**Strategy B**: filter `failed_ids` to only units in `middle_units` (not all units).

**Strategy C**: replace `latest_ids` guard with `middle_ids` — only trim assistant messages whose id is in `middle_ids`.

**Strategy D**: replace `latest_ids` guard with `middle_ids` — only trim tool messages whose id is in `middle_ids`.

---

## Step 8 — Update Strategy E

```python
def _strategy_e_binary_drop(self, messages, fits) -> list[LLMMessage] | None:
    units = _parse_reasoning_units(messages)
    _, middle_units, _ = self._get_middle_units(units)
    if not middle_units:
        return None

    lo, hi = 1, len(middle_units)   # full range (was capped at 10%)
    best: list[LLMMessage] | None = None

    while lo <= hi:
        k = (lo + hi) // 2
        candidate = self._drop_oldest_k(messages, units, middle_units, k)
        if fits(candidate):
            best = candidate
            hi = k - 1
        else:
            lo = k + 1

    return best  # None if no k satisfies fits
```

`_drop_oldest_k` signature stays the same.

---

## Step 9 — Update Strategy F

```python
def _strategy_f_summarize(self, messages) -> list[LLMMessage] | None:
    units = _parse_reasoning_units(messages)
    _, middle_units, _ = self._get_middle_units(units)
    if not middle_units:
        return None

    n_to_summarize = max(1, int(len(middle_units) * self._cfg.summary_ratio))
    summary_units = middle_units[:n_to_summarize]   # oldest n from middle

    summary_msgs = [m for u in summary_units for m in _unit_to_messages(u)]
    summary_msg = self._call_summary_llm(summary_msgs)
    if summary_msg is None:
        return None

    summary_ids = {id(m) for m in summary_msgs}
    result, inserted = [], False
    for m in messages:
        if id(m) in summary_ids:
            if not inserted:
                result.append(summary_msg)
                inserted = True
        else:
            result.append(m)
    return result
```

---

## Step 10 — Log summary response in `_call_summary_llm`

After `response = client.generate(summary_request)`, add:

```python
self._logger.info("Strategy F: summary LLM response", content=response.assistant_message.content)
```

---

## Step 11 — Unit Tests (`tests/unit/test_token_truncation.py`)

Use `pytest`. Imports: `ClaudeTokenEstimator`, `LLMMessage`, `LLMRequest`, `BudgetResult`, `RoleBudget`, `ReActTruncationConfig`, `ReActContextTruncator`, `ReasoningUnit`, `_parse_reasoning_units`, `_unit_to_messages`.

### Fixtures / helpers

- `make_unit(tool_name, arg_val, result, success=True)` — builds a `ReasoningUnit` with one tool call+result
- `make_truncator(cfg)` — builds a `ReActContextTruncator` with mocked budget manager (assistant=500, tool=500) and mocked llm_factory
- `units_to_messages(units)` — flattens units to message list

### Test cases

| Test | What it verifies |
|---|---|
| `test_config_defaults` | `ReActTruncationConfig()` has `keep_first_units=1`, `keep_last_units=3`, `summary_ratio=0.20` |
| `test_parse_reasoning_units` | assistant+tool messages grouped correctly |
| `test_strategy_b_removes_only_middle_failed` | 5 units, unit[1] (middle) failed + unit[4] (tail, protected) failed → only unit[1] removed |
| `test_strategy_b_empty_middle_unchanged` | 3 units with keep_first=1, keep_last=3 → middle empty → unchanged |
| `test_strategy_c_trims_only_middle_args` | head/tail args untouched, middle args trimmed |
| `test_strategy_d_trims_only_middle_results` | head/tail results untouched, middle results trimmed |
| `test_strategy_e_full_range` | enough units that old 10% cap would fail; full range finds solution |
| `test_strategy_e_returns_none_no_solution` | budget so tight even dropping all middle units fails → None |
| `test_strategy_e_empty_middle_returns_none` | fewer units than keep_first+keep_last → None |
| `test_strategy_f_uses_summary_ratio` | `summary_ratio=0.5`, 4 middle units → 2 units summarized |
| `test_strategy_f_minimum_one_unit` | `summary_ratio=0.01`, 2 middle units → 1 unit summarized |
| `test_strategy_f_empty_middle_returns_none` | not enough units → None |
| `test_call_summary_llm_logs_response` | mock LLM client; assert `logger.info` called with response content |

---

## Boundary Conditions

| Condition | Behavior |
|---|---|
| `len(units) <= keep_first + keep_last` | middle is `[]`; B/C/D return unchanged; E/F return `None` |
| `summary_ratio * len(middle) < 1` | `max(1, int(...))` ensures ≥1 unit summarized |
| Binary search finds no valid k | `best` stays `None`, returns `None` |
| `keep_last_units = 0` | `tail = []`, `middle = units[keep_first:]` |

---

## Verification

```bash
cd /Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent
python -m pytest tests/unit/test_token_truncation.py -v
```

All tests must pass with no errors.
