// +build integration_test

package database_test

import (
	"os"
)

var testDSN = os.Getenv("RUYFO_TEST_DSN")
