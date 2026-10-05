package fixtures;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class FailingTest {
    @Test
    void rejectsWrongTotal() {
        assertEquals(5, 2 + 2);
    }

    @Test
    void recordsUnexpectedErrors() {
        throw new IllegalStateException("Synthetic producer error");
    }

    @Test
    void continuesIndependentChecks() {
        assertEquals("ready", "ready");
    }
}
