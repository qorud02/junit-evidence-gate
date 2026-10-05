package fixtures;

import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FlakyTest {
    private static final AtomicInteger assertionAttempts = new AtomicInteger();
    private static final AtomicInteger errorAttempts = new AtomicInteger();

    @Test
    void passesAfterAnAssertionFailure() {
        assertTrue(assertionAttempts.incrementAndGet() > 1, "Synthetic first-attempt failure");
    }

    @Test
    void passesAfterAnException() {
        if (errorAttempts.incrementAndGet() == 1) {
            throw new IllegalStateException("Synthetic first-attempt error");
        }
    }
}
