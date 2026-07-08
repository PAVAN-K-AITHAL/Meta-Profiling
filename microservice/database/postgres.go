package database

import (
	"database/sql"
	"fmt"
	"log"
	"time"

	_ "github.com/lib/pq" // PostgreSQL driver — the _ means "import for side effects only"
)

// User represents a row from the users table.
// The json tags control how this struct gets converted to JSON.
// Think of it like a Java POJO with @JsonProperty annotations.
type User struct {
	ID    int    `json:"id"`
	Name  string `json:"name"`
	Email string `json:"email"`
}

// InitDB opens a connection pool to PostgreSQL and verifies it works.
// In Java terms: this is like creating a DataSource/ConnectionPool.
//
// Parameters:
//   - dsn: Data Source Name, e.g. "postgres://user:pass@host:5432/dbname?sslmode=disable"
//
// Returns:
//   - *sql.DB: a connection pool (NOT a single connection — Go manages the pool internally)
//   - error: nil if successful, otherwise describes what went wrong
func InitDB(dsn string) (*sql.DB, error) {
	// sql.Open doesn't actually connect — it just validates the DSN and creates the pool object.
	// Think of it like: DataSource ds = new PGDataSource(url) — lazy initialization.
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to open database: %w", err)
	}

	// Configure the connection pool
	db.SetMaxOpenConns(25)                 // Max 25 simultaneous connections (like HikariCP maxPoolSize)
	db.SetMaxIdleConns(10)                 // Keep 10 idle connections warm
	db.SetConnMaxLifetime(5 * time.Minute) // Recycle connections after 5 min

	// Ping actually tries to connect — this is where we find out if PostgreSQL is reachable.
	// We retry because PostgreSQL might still be starting up in Docker.
	var pingErr error
	for i := 0; i < 30; i++ { // Try for up to 30 seconds
		pingErr = db.Ping()
		if pingErr == nil {
			log.Println(" Connected to PostgreSQL")
			return db, nil
		}
		log.Printf(" Waiting for PostgreSQL (attempt %d/30): %v", i+1, pingErr)
		time.Sleep(1 * time.Second)
	}

	return nil, fmt.Errorf("failed to connect to database after 30 attempts: %w", pingErr)
}

// GetUsers fetches `limit` random users from the database.
// The ORDER BY RANDOM() forces a full table scan — intentionally expensive for our experiments.
//
// In Java terms: this is like a DAO method.
func GetUsers(db *sql.DB, limit int) ([]User, error) {
	// $1 is a parameterized placeholder (like ? in JDBC PreparedStatement)
	// This prevents SQL injection.
	rows, err := db.Query(
		"SELECT id, name, email FROM users ORDER BY RANDOM() LIMIT $1",
		limit,
	)
	if err != nil {
		return nil, fmt.Errorf("query failed: %w", err)
	}
	defer rows.Close() // IMPORTANT: always close rows when done (like closing a ResultSet in Java)

	// Build a slice (Go's dynamic array — like ArrayList in Java or list in Python)
	var users []User
	for rows.Next() { // Like while(rs.next()) in JDBC
		var u User
		// Scan reads columns into struct fields — like rs.getInt("id"), rs.getString("name")
		if err := rows.Scan(&u.ID, &u.Name, &u.Email); err != nil {
			return nil, fmt.Errorf("scan failed: %w", err)
		}
		users = append(users, u) // Like list.add(u) in Java
	}

	// Check if iteration itself had errors
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("rows iteration error: %w", err)
	}

	return users, nil
}
