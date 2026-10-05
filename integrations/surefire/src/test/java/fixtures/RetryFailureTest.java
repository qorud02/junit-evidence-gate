package fixtures;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.fail;

class RetryFailureTest {
    @Test
    void stillFailsAfterRetry() {
        fail("Synthetic repeated failure");
    }

    @Test
    void stillErrorsAfterRetry() {
        throw new IllegalStateException("Synthetic repeated error");
    }
}
