CREATE TABLE persons (
  id int NOT NULL AUTO_INCREMENT PRIMARY KEY,
  first_name varchar(255) NOT NULL DEFAULT '',
  last_name varchar(255) NOT NULL DEFAULT '',
  email_address varchar(255) NOT NULL DEFAULT '',
  maximum_people_with_bikes int NOT NULL DEFAULT 0,
  maximum_people_without_bikes int NOT NULL DEFAULT 0,
  maximum_bikes_with_people int NOT NULL DEFAULT 0,
  maximum_bikes_without_people int NOT NULL DEFAULT 0,
  can_drive_thursday_night boolean NOT NULL DEFAULT 0,
  can_drive_friday_morning boolean NOT NULL DEFAULT 0,
  biking_back_saturday boolean NOT NULL DEFAULT 0,
  going_home_friday boolean NOT NULL DEFAULT 0,
  latitude decimal(9,6) NOT NULL DEFAULT 0.0,
  longitude decimal(9,6) NOT NULL DEFAULT 0.0
)
