package fixtures

import (
	"testing"

	"github.com/fantashley/ruyfo-planner/pkg/ruyfo"
	"github.com/google/go-cmp/cmp"
)

var testFixture = Fixtures{
	Persons: []ruyfo.Person{
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
			Coordinates: []float64{
				36.169090,
				-115.140580,
			},
		},
	},
}

func TestGetAll(t *testing.T) {
	fixture, err := GetAll()
	if err != nil {
		t.Fatalf("Error getting all fixtures: %v", err)
	}

	if diff := cmp.Diff(fixture, testFixture); diff != "" {
		t.Errorf("Difference between expected and retrieved fixtures: %v", diff)
	}
}
