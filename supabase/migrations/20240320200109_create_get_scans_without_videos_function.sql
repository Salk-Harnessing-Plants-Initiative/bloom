-- Function for getting scan traits

CREATE OR REPLACE FUNCTION get_scans_without_videos() RETURNS TABLE (
    id BIGINT
)
AS $$
    SELECT id
    FROM cyl_scans
    WHERE ('cyl-videos/' || cyl_scans.id || '.mp4') NOT IN
    (
        SELECT storage.objects.name FROM storage.objects WHERE storage.objects.bucket_id = 'videos'
    );
$$ LANGUAGE SQL;
