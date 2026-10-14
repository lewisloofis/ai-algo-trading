package main

import (
	"reflect"
	"testing"
)

func TestHeldUnsubscribed(t *testing.T) {
	held := map[string]int{"BBB": 2, "AAA": 1, "CCC": 0, "DDD": 4}
	got := heldUnsubscribed(held, []string{"ddd", " AAA "})
	if want := []string{"BBB"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}
