-- E2E seed data (MySQL). Mounted to /docker-entrypoint-initdb.d/ by
-- .github/docker-compose.e2e.yml. Plaintext passwords are intentional:
-- throwaway test data only.
CREATE DATABASE IF NOT EXISTS e2e;
USE e2e;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL,
  email VARCHAR(128)
);

CREATE TABLE IF NOT EXISTS orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  amount DECIMAL(10, 2) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users (username, email) VALUES
  ('alice', 'alice@example.com'),
  ('bob', 'bob@example.com');

INSERT INTO orders (user_id, amount, status) VALUES
  (1, 99.50, 'paid'),
  (1, 20.00, 'pending'),
  (2, 150.00, 'paid');
