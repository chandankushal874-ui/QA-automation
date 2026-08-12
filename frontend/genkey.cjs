const crypto = require("crypto");
const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "pem" },
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
});
const fs = require("fs");
fs.writeFileSync("jwt_private.pem", privateKey);
fs.writeFileSync("jwt_public.pem", publicKey);
console.log("PRIVATE_KEY_START");
console.log(JSON.stringify(privateKey));
console.log("PRIVATE_KEY_END");
console.log("PUBLIC_KEY_START");
console.log(JSON.stringify(publicKey));
console.log("PUBLIC_KEY_END");
