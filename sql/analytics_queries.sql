-- Retail Sales analytics queries (SQLite).
-- Each query starts with a "-- name:" marker so src/analytics.py can run them one by one.
--
-- Notes:
--   * Every TransactionID is ONE sales line (one product and one quantity), not a complete
--     multi-item order. That is why we report an "average transaction amount", not an
--     "average order value".
--   * Money columns are INTEGER minor units (cents). SUM/AVG run on exact integers and the
--     result is divided by 100.0 only at the end, for display.

-- name: 1. Total revenue
SELECT ROUND(SUM(SalesAmountMinor) / 100.0, 2) AS TotalRevenue
FROM FactSales;

-- name: 2. Total number of transactions
SELECT COUNT(*) AS TotalTransactions
FROM FactSales;

-- name: 3. Average transaction amount
SELECT ROUND(AVG(SalesAmountMinor) / 100.0, 2) AS AverageTransactionAmount
FROM FactSales;

-- name: 4. Monthly revenue
SELECT d.Year,
       d.Month,
       d.MonthName,
       COUNT(*)                                AS Transactions,
       ROUND(SUM(f.SalesAmountMinor) / 100.0, 2) AS Revenue
FROM FactSales f
JOIN DimDate d ON d.DateKey = f.DateKey
GROUP BY d.Year, d.Month, d.MonthName
ORDER BY d.Year, d.Month;

-- name: 5. Top 5 products by revenue
SELECT p.ProductID,
       p.ProductName,
       p.Category,
       ROUND(SUM(f.SalesAmountMinor) / 100.0, 2) AS Revenue
FROM FactSales f
JOIN DimProduct p ON p.ProductKey = f.ProductKey
GROUP BY p.ProductKey, p.ProductID, p.ProductName, p.Category
ORDER BY SUM(f.SalesAmountMinor) DESC
LIMIT 5;

-- name: 6. Sales by product category
SELECT p.Category,
       COUNT(*)                                  AS Transactions,
       SUM(f.Quantity)                           AS UnitsSold,
       ROUND(SUM(f.SalesAmountMinor) / 100.0, 2) AS Revenue,
       ROUND(100.0 * SUM(f.SalesAmountMinor) / (SELECT SUM(SalesAmountMinor) FROM FactSales), 2) AS RevenueSharePct
FROM FactSales f
JOIN DimProduct p ON p.ProductKey = f.ProductKey
GROUP BY p.Category
ORDER BY SUM(f.SalesAmountMinor) DESC;

-- name: 7. Sales by region
SELECT c.Region,
       COUNT(*)                                  AS Transactions,
       SUM(f.Quantity)                           AS UnitsSold,
       ROUND(SUM(f.SalesAmountMinor) / 100.0, 2) AS Revenue
FROM FactSales f
JOIN DimCustomer c ON c.CustomerKey = f.CustomerKey
GROUP BY c.Region
ORDER BY SUM(f.SalesAmountMinor) DESC;

-- name: 8. Best-selling products by quantity (top 10)
SELECT p.ProductID,
       p.ProductName,
       SUM(f.Quantity) AS UnitsSold
FROM FactSales f
JOIN DimProduct p ON p.ProductKey = f.ProductKey
GROUP BY p.ProductKey, p.ProductID, p.ProductName
ORDER BY UnitsSold DESC, p.ProductName
LIMIT 10;

-- name: 9. Yearly sales summary
SELECT d.Year,
       COUNT(*)                                       AS Transactions,
       SUM(f.Quantity)                                AS UnitsSold,
       ROUND(SUM(f.SalesAmountMinor) / 100.0, 2)      AS Revenue,
       ROUND(AVG(f.SalesAmountMinor) / 100.0, 2)      AS AverageTransactionAmount
FROM FactSales f
JOIN DimDate d ON d.DateKey = f.DateKey
GROUP BY d.Year
ORDER BY d.Year;
