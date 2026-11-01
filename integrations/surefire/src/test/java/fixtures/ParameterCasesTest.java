package fixtures;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import static org.junit.jupiter.api.Assertions.assertEquals;

class ParameterCasesTest {
    @ParameterizedTest
    @ValueSource(strings = {"00123", "00456"})
    void keepsParameterIdentitiesDistinct(String identifier) {
        assertEquals(5, identifier.length());
    }
}
