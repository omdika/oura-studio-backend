-- Migration SQL for Version 3.51 - Add category column to product table
-- Run this on Supabase SQL Editor if needed, or it is automatically applied via Alembic migrations.

ALTER TABLE product ADD COLUMN category VARCHAR(50);
CREATE INDEX idx_product_category ON product(category);
