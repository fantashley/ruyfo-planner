package ruyfo

type Person struct {
	FirstName                 string    `json:"first_name"`
	LastName                  string    `json:"last_name"`
	EmailAddress              string    `json:"email_address"`
	MaximumPeopleWithBikes    int       `json:"maximum_people_with_bikes"`
	MaximumPeopleWithoutBikes int       `json:"maximum_people_without_bikes"`
	MaximumBikesWithPeople    int       `json:"maximum_bikes_with_people"`
	MaximumBikesWithoutPeople int       `json:"maximum_bikes_without_people"`
	CanDriveThursdayNight     bool      `json:"can_drive_thursday_night"`
	CanDriveFridayMorning     bool      `json:"can_drive_friday_morning"`
	BikingBackSaturday        bool      `json:"biking_back_saturday"`
	GoingHomeFriday           bool      `json:"going_home_friday"`
	Coordinates               []float64 `json:"coordinates"`
}
