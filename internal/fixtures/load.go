package fixtures

import (
	"encoding/json"
	"fmt"
	"io/ioutil"
	"path/filepath"

	"github.com/fantashley/ruyfo-planner/pkg/ruyfo"
)

type Fixtures struct {
	Persons []ruyfo.Person `json:"persons"`
}

const (
	DataDir      = "./data"
	DataFileGlob = "*.json"
)

func GetAll() (Fixtures, error) {
	allFixtures := Fixtures{}

	absPath, err := filepath.Abs(filepath.Join(DataDir, DataFileGlob))
	if err != nil {
		return allFixtures, fmt.Errorf("failed to get absolute file path: %w", err)
	}

	files, err := filepath.Glob(absPath)
	if err != nil {
		return allFixtures, fmt.Errorf("failed to get files matching glob %q: %w", absPath, err)
	}

	for _, file := range files {
		newFixture, err := GetFile(filepath.Base(file))
		if err != nil {
			return allFixtures, fmt.Errorf("failed to load fixture in file %q: %w", file, err)
		}

		allFixtures = allFixtures.Combine(newFixture)
	}

	return allFixtures, nil
}

func GetFile(filename string) (Fixtures, error) {
	var fixtures Fixtures

	fullPath, err := filepath.Abs(filepath.Join(DataDir, filename))
	if err != nil {
		return fixtures, fmt.Errorf("failed to get absolute file path: %w", err)
	}

	contents, err := ioutil.ReadFile(fullPath)
	if err != nil {
		return fixtures, fmt.Errorf("failed to read file %q: %w", fullPath, err)
	}

	if err = json.Unmarshal(contents, &fixtures); err != nil {
		return fixtures, fmt.Errorf("failed to unmarshal fixtures file %q: %w", fullPath, err)
	}

	return fixtures, nil
}

func (f Fixtures) Combine(newFixture Fixtures) Fixtures {
	combinedFixtures := f

	if len(newFixture.Persons) > 0 {
		if len(combinedFixtures.Persons) == 0 {
			combinedFixtures.Persons = make([]ruyfo.Person, 0, len(newFixture.Persons))
		}
		combinedFixtures.Persons = append(combinedFixtures.Persons, newFixture.Persons...)
	}

	return combinedFixtures
}
