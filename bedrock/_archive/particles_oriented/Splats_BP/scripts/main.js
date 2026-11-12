import SampleManager from "./SampleManager.js";
import { scriptBox } from "./ScriptBox.js";

const sm = new SampleManager();

sm.registerSamples({
  scriptBox: [scriptBox],
});