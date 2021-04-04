// +build integration_test

package database_test

import (
	"context"
	"testing"

	"github.com/fantashley/ruyfo-planner/internal/fixtures"
	"github.com/fantashley/ruyfo-planner/internal/testdb"
	"github.com/fantashley/ruyfo-planner/pkg/database"
)

func TestCreateAndListPersons(t *testing.T) {
	db := testdb.CreateTestDatabase(t, testDSN)
	defer db.Close(t)

	ctx := context.Background()
	fixtures := fixtures.GetFile(t, "testdata/persons.json")
	database := database.New(db.DB)

	for _, person := range fixtures.Persons {
		if err := database.CreatePerson(ctx, person); err != nil {
			t.Errorf("failed to create %s %s: %v", person.FirstName, person.LastName, err)
		}
	}
}
