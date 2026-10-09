package main

import "testing"

func TestGreeting(t *testing.T) {
	if got, want := greeting("x"), "Hello from x!"; got != want {
		t.Fatalf("greeting() = %q, want %q", got, want)
	}
}
