package fixtures

import (
	"encoding/json"
	"io/ioutil"
	"path/filepath"
	"testing"

	"github.com/fantashley/ruyfo-planner/pkg/entity"
)

type Fixtures struct {
	Persons []entity.Person `json:"persons"`
}

const (
	DefaultDataDir = "./testdata"
	DataFileGlob   = "*.json"
)

func GetAll(t *testing.T, datadir string) Fixtures {
	allFixtures := Fixtures{}

	actualDir := DefaultDataDir
	if datadir != "" {
		actualDir = datadir
	}

	absPath, err := filepath.Abs(filepath.Join(actualDir, DataFileGlob))
	if err != nil {
		t.Fatalf("failed to get absolute file path: %v", err)
	}

	files, err := filepath.Glob(absPath)
	if err != nil {
		t.Fatalf("failed to get files matching glob %q: %v", absPath, err)
	}

	for _, file := range files {
		newFixture := GetFile(t, file)
		allFixtures = allFixtures.Combine(newFixture)
	}

	return allFixtures
}

func GetFile(t *testing.T, filename string) Fixtures {
	var fixtures Fixtures

	fullPath, err := filepath.Abs(filename)
	if err != nil {
		t.Fatalf("failed to get absolute file path: %v", err)
	}

	contents, err := ioutil.ReadFile(fullPath)
	if err != nil {
		t.Fatalf("failed to read file %q: %v", fullPath, err)
	}

	if err = json.Unmarshal(contents, &fixtures); err != nil {
		t.Fatalf("failed to unmarshal fixtures file %q: %v", fullPath, err)
	}

	return fixtures
}

func (f Fixtures) Combine(newFixture Fixtures) Fixtures {
	combinedFixtures := f

	if len(newFixture.Persons) > 0 {
		if len(combinedFixtures.Persons) == 0 {
			combinedFixtures.Persons = make([]entity.Person, 0, len(newFixture.Persons))
		}
		combinedFixtures.Persons = append(combinedFixtures.Persons, newFixture.Persons...)
	}

	return combinedFixtures
}
