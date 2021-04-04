package testdb

import (
	"database/sql"
	"math/rand"
	"path"
	"strings"
	"testing"
	"time"

	"github.com/fantashley/ruyfo-planner/internal/migrations"
	"github.com/go-sql-driver/mysql"
)

const defaultDSN = "root:@tcp(127.0.0.1:3306)/"

type TestDB struct {
	Name string
	DB   *sql.DB
}

func CreateTestDatabase(t *testing.T, dsn string) TestDB {
	actualDSN := defaultDSN
	if dsn != "" {
		actualDSN = dsn
	}

	db, err := sql.Open("mysql", actualDSN)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}

	name := generateRandomName("testdb")

	_, err = db.Exec("CREATE DATABASE " + name)
	if e, ok := err.(*mysql.MySQLError); ok {
		t.Fatalf("failed to create DB %q: %v", name, e.Message)
	}

	_, err = db.Exec("USE " + name)
	if err != nil {
		t.Fatalf("failed to use DB %q", name)
	}

	actualDSN = path.Join(actualDSN, name)
	actualDSN = "mysql://" + actualDSN

	if err = migrations.RunMigrations(actualDSN); err != nil {
		t.Fatalf("failed to run migrations: %v", err)
	}

	return TestDB{
		Name: name,
		DB:   db,
	}
}

func (tdb TestDB) Close(t *testing.T) {
	defer tdb.DB.Close()

	_, err := tdb.DB.Exec("DROP DATABASE " + tdb.Name)
	if err != nil {
		t.Fatalf("failed to drop DB %q", tdb.Name)
	}
}

func generateRandomName(prefix string) string {
	var (
		sb           = strings.Builder{}
		src          = rand.NewSource(time.Now().UnixNano())
		suffixLength = 15
		asciiA       = 65
	)

	sb.WriteString(prefix + "_")

	for i := 0; i < suffixLength; i++ {
		letter := byte(src.Int63()%26 + int64(asciiA))
		sb.WriteByte(letter)
	}

	return sb.String()
}
