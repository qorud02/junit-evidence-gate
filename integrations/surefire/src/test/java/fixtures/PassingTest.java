package fixtures;

import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class PassingTest {
    @Test
    void addsWholeNumbers() {
        assertEquals(4, 2 + 2);
    }

    @Test
    void keepsZeroPrefixedIdentifier() {
        assertEquals(5, "00123".length());
    }

    @Test
    @Disabled("Synthetic optional check")
    void optionalRemoteCheck() {
        throw new AssertionError("A disabled test must not execute");
    }
}
