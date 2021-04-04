package testdb

import (
	"database/sql"
	"fmt"
	"math/rand"
	"strings"
	"time"
)

const defaultDSN = "root:@tcp(127.0.0.1:3306)/"

type TestDB struct {
	Name string
	DB   *sql.DB
}

func CreateTestDatabase(dsn string) (TestDB, error) {
	actualDSN := defaultDSN
	if dsn != "" {
		actualDSN = dsn
	}

	db, err := sql.Open("mysql", actualDSN)
	if err != nil {
		return TestDB{}, fmt.Errorf("failed to open database: %w", err)
	}

	name := generateRandomName("testdb")

	_, err = db.Exec("CREATE DATABASE ?", name)
	if err != nil {
		return TestDB{}, fmt.Errorf("failed to create DB %q", name)
	}

	_, err = db.Exec("USE DATABASE ?", name)
	if err != nil {
		return TestDB{}, fmt.Errorf("failed to use DB %q", name)
	}

	return TestDB{
		Name: name,
		DB:   db,
	}, nil
}

func (tdb TestDB) Close() error {
	defer tdb.DB.Close()

	const sql = "DROP DATABASE ?"
	_, err := tdb.DB.Exec(sql, tdb.Name)
	if err != nil {
		return fmt.Errorf("failed to drop DB %q", tdb.Name)
	}

	return nil
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
