set statement_timeout TO '0';


-- create cyl_traits table to track metadata for trait types
CREATE TABLE IF NOT EXISTS public.cyl_traits (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_cyl_traits_name ON public.cyl_traits(name);


-- grant permissions for cyl_traits table
ALTER TABLE cyl_traits ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated users can insert cyl_traits" ON cyl_traits;
CREATE POLICY "Authenticated users can insert cyl_traits"
ON cyl_traits AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can select cyl_traits" ON cyl_traits;
CREATE POLICY "Authenticated users can select cyl_traits"
ON cyl_traits AS permissive
FOR SELECT TO authenticated
USING (true);

DROP POLICY IF EXISTS "Authenticated users can update cyl_traits" ON cyl_traits;
CREATE POLICY "Authenticated users can update cyl_traits"
ON cyl_traits AS permissive
FOR UPDATE TO authenticated
USING (true);

DROP POLICY IF EXISTS "Authenticated users can delete cyl_traits" ON cyl_traits;
CREATE POLICY "Authenticated users can delete cyl_traits"
ON cyl_traits AS permissive
FOR DELETE TO authenticated
USING (true);
