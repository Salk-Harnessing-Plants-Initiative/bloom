-- users
INSERT INTO auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at, confirmation_sent_at, last_sign_in_at, raw_app_meta_data, raw_user_meta_data, created_at, updated_at, email_change_confirm_status, is_sso_user, confirmation_token, email_change, email_change_token_new, recovery_token) VALUES
('00000000-0000-0000-0000-000000000000', 'a90dfcb8-4402-46cb-9def-29a73e84e6d6', 'authenticated', 'authenticated', 'bloom-user@salk.edu', '$2a$10$vB1w9OIkuNOt4sMkyNbiU.gr2tH9y9r0X89G.DwvicV5nuj7yb4jK', '2023-08-05 03:08:55.226834+00', '2023-08-05 03:08:43.044418+00', '2023-08-05 03:08:56.75363+00', '{"provider":"email","providers":["email"]}', '{}', '2023-08-05 03:08:42.998526+00', '2023-08-05 03:08:56.814193+00', 0, 'FALSE', '', '', '', '')
ON CONFLICT (id) DO NOTHING;

-- identities
INSERT INTO auth.identities (id, provider, user_id, identity_data, last_sign_in_at, created_at, updated_at) VALUES
('a90dfcb8-4402-46cb-9def-29a73e84e6d6', 'email', 'a90dfcb8-4402-46cb-9def-29a73e84e6d6', '{"sub":"a90dfcb8-4402-46cb-9def-29a73e84e6d6","email":"bloom-user@salk.edu"}', '2023-08-07 15:30:33.811315+00', '2023-08-07 15:30:33.811344+00', '2023-08-07 15:30:33.811344+00')
ON CONFLICT (provider, id) DO NOTHING;

-- scanners

INSERT INTO cyl_scanners (id, name)
VALUES 
  (1, 'FastScanner'),
  (2, 'SlowScanner');
