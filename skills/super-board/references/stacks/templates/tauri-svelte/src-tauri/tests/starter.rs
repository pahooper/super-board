// Starter test from super-board's scaffold: proves `cargo test` builds the
// Tauri core and runs. Replace it with a real test of your commands.
#[test]
fn starter_test_runs() {
    let doubled: Vec<i32> = [1, 2, 3].iter().map(|n| n * 2).collect();
    assert_eq!(doubled, vec![2, 4, 6]);
}
