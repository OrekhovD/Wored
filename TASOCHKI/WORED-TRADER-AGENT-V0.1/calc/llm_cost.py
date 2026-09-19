# Ollama Cloud prices, USD per 1M tokens (ollama.com/pricing, checked 2026-09-16)
P = {
 "glm-5.2": (1.40, 0.26, 4.40), "glm-5.3": (1.40, 0.26, 4.40), "glm-5.3-flash": (0.15, 0.03, 0.50),
 "deepseek-v4.1-flash": (0.15, 0.003, 0.60), "deepseek-v4-pro": (0.66, 0.022, 1.98),
 "minimax-m2.7": (0.30, 0.06, 1.20), "kimi-k2.6": (0.95, 0.16, 4.00),
}
PEAK = {"deepseek-v4.1-flash", "deepseek-v4-pro"}   # x2 12:00-18:00 UTC Mon-Fri
PEAK_SHARE = (6/24) * (5/7)                          # ~17.9% of wall time
# role, model, calls/day, input tokens, cached share, output tokens
roles = [
 ("Trader-Orchestrator: часовой план", "glm-5.2", 24, 10000, 0.6, 1200),
 ("Trader-Orchestrator: внеплановые события", "glm-5.2", 6, 10000, 0.6, 1200),
 ("Market-Scout: деривативы/режим", "deepseek-v4.1-flash", 24, 6000, 0.5, 600),
 ("Forecast-Critic: ревью прогноза", "minimax-m2.7", 24, 6000, 0.5, 600),
 ("Entry-Gate: проверка сигнала", "deepseek-v4.1-flash", 150, 3000, 0.66, 200),
 ("Journal-Coach: разбор сделки", "glm-5.3-flash", 80, 3000, 0.5, 400),
 ("Daily-Review: итоги дня", "glm-5.2", 1, 60000, 0.3, 4000),
 ("Weekly-Audit: аудит стратегии (1/7)", "deepseek-v4-pro", 1/7, 100000, 0.2, 8000),
 ("Hermes ops: Telegram/диагностика", "glm-5.2", 8, 25000, 0.7, 1500),
]
print("role\tmodel\tcalls_day\tin_tokens\tcached_share\tout_tokens\tusd_call\tusd_day\tusd_month")
tot = 0
for name, m, n, tin, cs, tout in roles:
    pi, pc, po = P[m]
    c = (tin*(1-cs)*pi + tin*cs*pc + tout*po) / 1e6
    if m in PEAK: c *= (1 + PEAK_SHARE)
    day = c*n; tot += day
    print(f"{name}\t{m}\t{n:.2f}\t{tin}\t{cs}\t{tout}\t{c:.5f}\t{day:.4f}\t{day*30:.2f}")
print(f"TOTAL\t\t\t\t\t\t\t{tot:.4f}\t{tot*30:.2f}")
reqs = sum(r[2] for r in roles)
print("requests/day", round(reqs,1))
