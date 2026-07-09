INSERT INTO budgets (month_date, point_budget, conversion_rate)
VALUES (date_trunc('month', CURRENT_DATE)::date, 100, 1.00)
ON CONFLICT (month_date) DO NOTHING;
