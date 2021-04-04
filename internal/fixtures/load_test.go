package fixtures

import (
	"testing"

	"github.com/fantashley/ruyfo-planner/pkg/entity"
	"github.com/google/go-cmp/cmp"
)

var testFixture = Fixtures{
	Persons: []entity.Person{
		{
			FirstName:                 "Ashley",
			LastName:                  "Nelson",
			EmailAddress:              "fant@shley.email",
			MaximumPeopleWithBikes:    5,
			MaximumPeopleWithoutBikes: 5,
			MaximumBikesWithPeople:    0,
			MaximumBikesWithoutPeople: 0,
			CanDriveThursdayNight:     true,
			CanDriveFridayMorning:     true,
			BikingBackSaturday:        false,
			GoingHomeFriday:           false,
			Latitude:                  36.169090,
			Longitude:                 -115.140580,
		},
	},
}

func TestGetAll(t *testing.T) {
	fixture := GetAll(t, "")

	if diff := cmp.Diff(fixture.Persons[0], testFixture.Persons[0]); diff != "" {
		t.Errorf("Difference between expected and retrieved fixtures: %v", diff)
	}

	expected := 3
	actual := len(fixture.Persons)
	if actual != expected {
		t.Errorf("Expected %d persons, got %d", expected, actual)
	}
}

func TestGetFile(t *testing.T) {
	const personsFile = "testdata/persons.json"

	fixture := GetFile(t, personsFile)

	if diff := cmp.Diff(fixture.Persons[0], testFixture.Persons[0]); diff != "" {
		t.Errorf("Difference between expected and retrieved fixtures: %v", diff)
	}

	expected := 3
	actual := len(fixture.Persons)
	if actual != expected {
		t.Errorf("Expected %d persons, got %d", expected, actual)
	}
}
