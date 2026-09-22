-- Дамп точек последних завершённых прогнозов для
-- scripts/probe_forecast_role_coverage.py. Запуск из прод-стека:
--
--   Get-Content scripts\probe_forecast_role_coverage.sql |
--     docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
--
-- Вывод перенаправить в JSON-файл (на Windows ещё и -Width 200000 у Out-File,
-- иначе PowerShell перенесёт длинный JSON и json.loads не соберёт строку).

\pset format unaligned
\pset tuples_only on

SELECT json_agg(row_to_json(t))
FROM (
  SELECT p.request_id, p.step_index, p.target_time, p.predicted_price, p.predicted_high,
         p.predicted_low, p.confidence, p.predicted_change_pct,
         p.model_run_id AS run_id, r.agent_role, r.model_id
  FROM forecast_points p
  -- LEFT JOIN: 47 прогонов в прод-БД без agent_role должны остаться в дампе,
  -- иначе прогон никогда не проверит ветку legacy-no-roles.
  LEFT JOIN forecast_model_runs r ON r.id = p.model_run_id
  WHERE p.request_id IN (
    SELECT id FROM forecast_requests
    WHERE status = 'completed'
    ORDER BY created_at DESC
    LIMIT 5
  )
  ORDER BY p.request_id, p.step_index, p.model_run_id
) t;
