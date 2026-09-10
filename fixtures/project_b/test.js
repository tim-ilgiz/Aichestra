const { add } = require("./src/add");

if (add(2, 3) !== 5) {
  console.error("add failed");
  process.exit(1);
}
console.log("ok");
process.exit(0);
