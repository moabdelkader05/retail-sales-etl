-- Retail Sales Data Warehouse: star schema for SQLite.
-- Every statement is idempotent (IF NOT EXISTS) so the schema can be applied on each run.
-- Foreign keys are enforced per connection with PRAGMA foreign_keys = ON (see src/load.py).

CREATE TABLE IF NOT EXISTS DimProduct (
    ProductKey   INTEGER PRIMARY KEY AUTOINCREMENT,
    ProductID    TEXT    NOT NULL UNIQUE,
    ProductName  TEXT    NOT NULL,
    Category     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS DimCustomer (
    CustomerKey  INTEGER PRIMARY KEY AUTOINCREMENT,
    CustomerID   TEXT    NOT NULL UNIQUE,
    Region       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS DimDate (
    DateKey      INTEGER PRIMARY KEY,              -- YYYYMMDD, e.g. 20240315
    FullDate     TEXT    NOT NULL UNIQUE,          -- ISO 8601, e.g. 2024-03-15
    Day          INTEGER NOT NULL CHECK (Day BETWEEN 1 AND 31),
    Month        INTEGER NOT NULL CHECK (Month BETWEEN 1 AND 12),
    MonthName    TEXT    NOT NULL,
    Quarter      INTEGER NOT NULL CHECK (Quarter BETWEEN 1 AND 4),
    Year         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS FactSales (
    TransactionID TEXT    NOT NULL PRIMARY KEY,    -- business key, guarantees no duplicates
    ProductKey    INTEGER NOT NULL REFERENCES DimProduct  (ProductKey),
    CustomerKey   INTEGER NOT NULL REFERENCES DimCustomer (CustomerKey),
    DateKey       INTEGER NOT NULL REFERENCES DimDate     (DateKey),
    Quantity         INTEGER NOT NULL CHECK (Quantity > 0),
    -- Money is stored as INTEGER minor units (cents): 19.99 is stored as 1999.
    -- This avoids floating point rounding errors; queries divide by 100.0 only for display.
    UnitPriceMinor   INTEGER NOT NULL CHECK (UnitPriceMinor > 0),
    SalesAmountMinor INTEGER NOT NULL CHECK (SalesAmountMinor > 0),
    CHECK (SalesAmountMinor = Quantity * UnitPriceMinor)
);

CREATE INDEX IF NOT EXISTS idx_factsales_product  ON FactSales (ProductKey);
CREATE INDEX IF NOT EXISTS idx_factsales_customer ON FactSales (CustomerKey);
CREATE INDEX IF NOT EXISTS idx_factsales_date     ON FactSales (DateKey);
