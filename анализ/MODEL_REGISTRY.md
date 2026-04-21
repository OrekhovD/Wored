# MODEL REGISTRY — HTX Trading Bot
# Последняя проверка: 18.04.2026
# Источник: скриншоты аккаунта + официальные доки

## СТАТУС МОДЕЛЕЙ

### 1. Qwen / Alibaba DashScope ✅ ПОДТВЕРЖДЕНО

| Model String | Квота (18.04.26) | Истекает | Thinking | Рекомендация |
|---|---|---|---|---|
| `qwen3.6-plus` | 505,181 / 1,000,000 | 2026-06-04 | ✅ | **Основной** |
| `qwen3-max` | 1,000,000 / 1,000,000 | 2026-06-04 | ✅ | Сложный reasoning |
| `qwen3.5-plus` | 985,012 / 1,000,000 | 2026-06-04 | ✅ | Резерв |
| `qwen3.5-plus-2026-02-15` | 991,314 / 1,000,000 | 2026-06-04 | ✅ | Snapshot версия |
| `qwen3.5-122b-a10b` | 1,000,000 / 1,000,000 | 2026-06-04 | ? | MoE вариант |
| `qwen2.5-7b-instruct-1m` | 1,000,000 / 1,000,000 | 2026-06-04 | ❌ | Быстрый/дешёвый |
| `qwen3-vl-235b-a22b-thinking` | 1,000,000 / 1,000,000 | 2026-06-04 | ✅ | Vision задачи |

**⚠️ ВНИМАНИЕ:** `qwen-coder-3-plus` из TZ.md **не найден** в аккаунте.
Заменить на `qwen2.5-coder-32b-instruct` или `qwen3-max`.

**Endpoint:** `https://dashscope.aliyuncs.com/compatible-mode/v1`
**Docs:** https://www.alibabacloud.com/help/en/model-studio/

---

### 2. GLM / ZhipuAI ⚠️ ТРЕБУЕТ ПРОВЕРКИ

**Ключ:** формат `xxxxxxxx.XxxxxxXXXX` — это ZhipuAI (BigModel) ✅

**⚠️ ВНИМАНИЕ:** В TZ.md используется model string `glm-5.1`.
Актуальные model strings ZhipuAI (нужно проверить в консоли):
- `glm-4-plus` — флагман апрель 2026 (проверить)
- `glm-z1-plus` — reasoning модель
- Точный string GLM-5 / GLM-5.1 — уточнить на https://bigmodel.cn/

**Endpoint:** `https://open.bigmodel.cn/api/paas/v4/`

---

### 3. MiniMax ⚠️ НЕСТАНДАРТНЫЙ КЛЮЧ

**Ключ:** формат `nvapi-xxxxx` — это **NVIDIA NIM API Key**, не нативный MiniMax!

**Возможные варианты:**
- A) Это доступ к MiniMax через NVIDIA NIM каталог
- B) Это другая модель через NVIDIA (Llama, Mistral и т.д.)

**Действия:** Зайти на https://build.nvidia.com/explore/discover
и найти MiniMax в каталоге → взять точный model string.

**Endpoint NVIDIA NIM:** `https://integrate.api.nvidia.com/v1`

---

### 4. Google AI Studio (Gemini) ✅ БОНУС

**Ключ:** формат `AQ.Ab8Rxxxxx` — Google AI Studio API Key ✅

**Доступные модели (бесплатный tier):**
- `gemini-2.0-flash` — быстрый, большой контекст
- `gemini-2.5-pro` (если доступен)

**Endpoint (OpenAI-compatible):**
`https://generativelanguage.googleapis.com/v1beta/openai/`

**Применение в боте:** замена Perplexity для быстрых ответов ИЛИ
добавить как 5-ю модель для мультимодального анализа.

---

## РЕКОМЕНДУЕМЫЙ РОУТИНГ (обновлено)

```
market_news    → Perplexity Sonar Pro (если есть ключ)
               → Gemini 2.0 Flash (fallback, через web search tool)
deep_analysis  → GLM-5 (если model string подтверждён)
               → Qwen3-Max (fallback)
backtest_code  → Qwen3.6-Plus с enable_thinking=True
               → Qwen3-Max (если coder недоступен)
quick_chat     → Qwen3.6-Plus (основная квота)
               → MiniMax/NVIDIA (если endpoint подтверждён)
position_calc  → Qwen3.6-Plus (математика)
```

---

## TODO ДО ПЕРВОГО ЗАПУСКА

- [ ] Ротация ВСЕХ ключей (telegram, htx, dashscope, glm, minimax, google)
- [ ] Проверить model string GLM на https://bigmodel.cn/console
- [ ] Проверить MiniMax model string в NVIDIA NIM каталоге
- [ ] Проверить наличие `qwen2.5-coder-32b-instruct` в DashScope аккаунте
- [ ] Сохранить ключи в .env (не в файлы проекта!)
