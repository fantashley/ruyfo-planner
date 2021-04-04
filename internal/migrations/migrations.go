package migrations

import (
	"embed"
	"fmt"
	"net/http"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/mysql"
	"github.com/golang-migrate/migrate/v4/source/httpfs"
)

//go:embed sql
var DBMigrationFS embed.FS

func RunMigrations(dbURL string) error {
	source, err := httpfs.New(http.FS(DBMigrationFS), "sql")
	if err != nil {
		return fmt.Errorf("failed to get miagration source from embed filesystem: %w", err)
	}

	m, err := migrate.NewWithSourceInstance("httpfs", source, dbURL)
	if err != nil {
		return fmt.Errorf("failed to create migration: %w", err)
	}

	if err = m.Up(); err != nil {
		return fmt.Errorf("failed to run migration: %w", err)
	}

	return nil
}
