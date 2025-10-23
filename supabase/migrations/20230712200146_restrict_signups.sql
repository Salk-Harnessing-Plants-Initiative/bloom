CREATE OR REPLACE FUNCTION check_email_domain() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.email NOT LIKE '%@salk.edu' THEN
        RAISE EXCEPTION 'Only @salk.edu email addresses are allowed.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER check_email_trigger 
BEFORE INSERT OR UPDATE OF email ON auth.users 
FOR EACH ROW EXECUTE PROCEDURE check_email_domain();
