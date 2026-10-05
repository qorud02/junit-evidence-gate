describe('integer values', () => {
  test('adds whole numbers', () => {
    expect(2 + 2).toBe(4);
  });

  test('preserves a zero-prefixed identifier', () => {
    expect('00123').toHaveLength(5);
  });

  test.skip('optional remote check', () => {
    throw new Error('A skipped test must not execute');
  });
});
