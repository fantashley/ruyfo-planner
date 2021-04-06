// +build integration_test

package database_test

import (
	"context"
	"testing"

	"github.com/fantashley/ruyfo-planner/internal/fixtures"
	"github.com/fantashley/ruyfo-planner/internal/testdb"
	"github.com/fantashley/ruyfo-planner/pkg/database"
	"github.com/google/go-cmp/cmp"
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

	persons, err := database.ListPersons(ctx)
	if err != nil {
		t.Fatalf("failed to list persons: %v", err)
	}

	for i, person := range persons {
		if diff := cmp.Diff(person, fixtures.Persons[i]); diff != "" {
			t.Errorf("difference in persons at index %d: %s", i, diff)
		}
	}
}
