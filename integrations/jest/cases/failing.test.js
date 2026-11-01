describe('invalid output', () => {
  test('rejects a wrong total', () => {
    expect(2 + 2).toBe(5);
  });

  test('continues independent checks', () => {
    expect('ready').toBe('ready');
  });
});
