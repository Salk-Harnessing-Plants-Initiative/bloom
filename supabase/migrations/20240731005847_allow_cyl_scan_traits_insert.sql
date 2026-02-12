DROP POLICY IF EXISTS "Authenticated users can insert cyl_scan_traits" ON cyl_scan_traits;
CREATE POLICY "Authenticated users can insert cyl_scan_traits"
ON cyl_scan_traits AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);
