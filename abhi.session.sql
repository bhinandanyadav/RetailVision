
-- Update admin email
UPDATE users
SET email = 'rabhyayadav14@gmail.com'
WHERE LOWER(email) = 'rabhya21@gmail.com';

-- Verify result
SELECT id, name, email, role
FROM users
WHERE LOWER(email) IN ('rabhya21@gmail.com', 'rabhyayadav14@gmail.com');

