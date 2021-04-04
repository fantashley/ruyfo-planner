package entity

type Person struct {
	ID                        int     `json:"id" db:"id"`
	FirstName                 string  `json:"first_name" db:"first_name"`
	LastName                  string  `json:"last_name" db:"last_name"`
	EmailAddress              string  `json:"email_address" db:"email_address"`
	MaximumPeopleWithBikes    int     `json:"maximum_people_with_bikes" db:"maximum_people_with_bikes"`
	MaximumPeopleWithoutBikes int     `json:"maximum_people_without_bikes" db:"maximum_people_without_bikes"`
	MaximumBikesWithPeople    int     `json:"maximum_bikes_with_people" db:"maximum_bikes_with_people"`
	MaximumBikesWithoutPeople int     `json:"maximum_bikes_without_people" db:"maximum_bikes_without_people"`
	CanDriveThursdayNight     bool    `json:"can_drive_thursday_night" db:"can_drive_thursday_night"`
	CanDriveFridayMorning     bool    `json:"can_drive_friday_morning" db:"can_drive_friday_morning"`
	BikingBackSaturday        bool    `json:"biking_back_saturday" db:"biking_back_saturday"`
	GoingHomeFriday           bool    `json:"going_home_friday" db:"going_home_friday"`
	Latitude                  float64 `json:"latitude" db:"latitude"`
	Longitude                 float64 `json:"longitude" db:"longitude"`
}
