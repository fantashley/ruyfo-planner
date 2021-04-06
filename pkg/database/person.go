package database

import (
	"context"
	"fmt"

	"github.com/fantashley/ruyfo-planner/pkg/entity"
)

func (db Database) CreatePerson(ctx context.Context, person entity.Person) error {
	// sql := "INSERT INTO cities(name, population) VALUES ('Moscow', 12506000)"
	const sql = `INSERT INTO persons(
		first_name,
		last_name,
		email_address,
		maximum_people_with_bikes,
		maximum_people_without_bikes,
		maximum_bikes_with_people,
		maximum_bikes_without_people,
		num_bikes,
		can_drive_thursday_night,
		can_drive_friday_morning,
		biking_back_saturday,
		going_home_friday,
		latitude,
		longitude
	) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`

	if _, err := db.ExecContext(
		ctx,
		sql,
		person.FirstName,
		person.LastName,
		person.EmailAddress,
		person.MaximumPeopleWithBikes,
		person.MaximumPeopleWithoutBikes,
		person.MaximumBikesWithPeople,
		person.MaximumBikesWithoutPeople,
		person.NumBikes,
		person.CanDriveThursdayNight,
		person.CanDriveFridayMorning,
		person.BikingBackSaturday,
		person.GoingHomeFriday,
		person.Latitude,
		person.Longitude,
	); err != nil {
		return fmt.Errorf("failed to create Person: %w", err)
	}

	return nil
}

func (db Database) ListPersons(ctx context.Context) ([]entity.Person, error) {
	var persons []entity.Person
	const sql = "SELECT * FROM persons"

	if err := db.SelectContext(
		ctx,
		&persons,
		sql,
	); err != nil {
		return persons, fmt.Errorf("failed to list persons: %w", err)
	}

	return persons, nil
}
