#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN 9
#define SS_PIN 10

MFRC522 mfrc522(SS_PIN, RST_PIN);
MFRC522::MIFARE_Key key;

String licensePlate = "";
String balance = "";

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  // Default key for authentication
  for (byte i = 0; i < 6; i++) key.keyByte[i] = 0xFF;

  Serial.println(F("=== RFID WRITER ==="));
  Serial.println(F("Enter license plate (Max 16 chars, A-Z/0-9 only):"));
}

void loop() {
  if (licensePlate == "") {
    if (Serial.available()) {
      licensePlate = Serial.readStringUntil('\n');
      licensePlate.trim();
      if (!isValidLicensePlate(licensePlate)) {
        Serial.println(F("❌ Invalid license plate! Try again:"));
        licensePlate = "";
      } else {
        Serial.println(F("✅ Valid license plate. Now enter balance (digits only):"));
      }
    }
    return;
  }

  if (balance == "") {
    if (Serial.available()) {
      balance = Serial.readStringUntil('\n');
      balance.trim();
      if (!isValidBalance(balance)) {
        Serial.println(F("❌ Invalid balance! Must be numeric. Try again:"));
        balance = "";
      } else {
        Serial.println(F("✅ Ready to write. Place your card on the reader..."));
      }
    }
    return;
  }

  // Check if card is present
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial())
    return;

  // Write license plate to block 2
  bool plateSuccess = writeBlock(2, licensePlate);
  bool balanceSuccess = writeBlock(4, balance);

  if (plateSuccess && balanceSuccess) {
    Serial.println(F("✅ Data written successfully to the card!"));
  } else {
    Serial.println(F("❌ Failed to write to one or more blocks."));
  }

  // Reset values for next write
  licensePlate = "";
  balance = "";

  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  delay(2000);
  Serial.println(F("\nReady for new entry. Enter license plate:"));
}

bool writeBlock(byte blockNumber, String data) {
  byte buffer[16];
  data.trim();
  data = data.substring(0, 16);  // Limit to 16 characters

  // Pad with spaces if needed
  for (int i = 0; i < 16; i++) {
    buffer[i] = (i < data.length()) ? data[i] : ' ';
  }

  // Authenticate
  MFRC522::StatusCode status = mfrc522.PCD_Authenticate(
    MFRC522::PICC_CMD_MF_AUTH_KEY_A, blockNumber, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.print(F("Auth error at block "));
    Serial.print(blockNumber);
    Serial.print(F(": "));
    Serial.println(mfrc522.GetStatusCodeName(status));
    return false;
  }

  // Write block
  status = mfrc522.MIFARE_Write(blockNumber, buffer, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.print(F("Write failed at block "));
    Serial.print(blockNumber);
    Serial.print(F(": "));
    Serial.println(mfrc522.GetStatusCodeName(status));
    return false;
  }

  return true;
}

bool isValidLicensePlate(String input) {
  if (input.length() == 0 || input.length() > 16) return false;
  for (char c : input) {
    if (!isalnum(c) && c != ' ') return false;
  }
  return true;
}

bool isValidBalance(String input) {
  if (input.length() == 0 || input.length() > 16) return false;
  for (char c : input) {
    if (!isdigit(c)) return false;
  }
  return true;
}
