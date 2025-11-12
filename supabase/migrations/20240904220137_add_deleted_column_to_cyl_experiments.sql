-- Add deleted column to cyl_experiments

ALTER TABLE cyl_experiments
ADD COLUMN deleted BOOLEAN DEFAULT FALSE;
