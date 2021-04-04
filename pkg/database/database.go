package database

import (
	"database/sql"

	"github.com/jmoiron/sqlx"
)

type DB interface {
	sqlx.ExecerContext
	sqlx.QueryerContext
}

type Database struct {
	*sqlx.DB
}

func New(db *sql.DB) Database {
	return Database{db: sqlx.NewDb(db, "mysql")}
}
