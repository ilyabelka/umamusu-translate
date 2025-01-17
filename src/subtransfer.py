import common
import ass
import srt
import re
from Levenshtein import ratio
from enum import Enum, auto

class SubFormat(Enum):
    NONE = auto()
    SRT = auto()
    ASS = auto()
    TXT = auto()
class Options(Enum):
    OVERRIDE_NAMES = "ovrNames"
    DUPE_CHECK_ALL = "dupeCheckAll"
    FILTER = "filter"


class TextLine:
    def __init__(self, text, name = "", effect = "") -> None:
        self.text: str = text
        self.name: str = name.lower()
        self.effect: str = effect.lower()

    def isChoice(self) -> bool:
        return self.effect == "choice"

# class Options():
#     def __init__(self, opts: dict) -> None:
#         for arg, val in opts.items():
#             if arg == "filter":
#                 val = val.split(",")
#             setattr(self, arg, val)
#     def __getattr__(self, attr):
#         return None

class BasicSubProcessor:
    #todo: Make options optional and deal with defaults here
    def __init__(self, srcFile, options: dict) -> None:
        self.srcFile = common.TranslationFile(srcFile)
        self.srcLines = self.srcFile.getTextBlocks()
        self.subLines: list[TextLine] = list()
        self.format = SubFormat.NONE
        if options[Options.FILTER]:
            options[Options.FILTER] = options[Options.FILTER].split(",")
        self.options = options
        self.skipNames = ["<username>", "", "モノローグ"]

    def saveSrc(self):
        self.srcFile.save()

    def getJp(self, idx):
        return self.srcLines[idx]['jpText']
    def getEn(self, idx):
        return TextLine(self.srcLines[idx]['enText'], self.srcLines[idx]['enName'])
    def setEn(self, idx, line: TextLine):
        self.srcLines[idx]['enText'] = self.filter(line.text, self.srcLines[idx])
        if "jpName" in self.srcLines[idx]:
            if self.srcLines[idx]['jpName'] in self.skipNames:
                self.srcLines[idx]['enName'] = "" # forcefully clear names that should not be translated
            elif line.name and (not self.srcLines[idx]['enName'] or self.options[Options.OVERRIDE_NAMES]):
                self.srcLines[idx]['enName'] = line.name

    def getChoices(self, idx):
        if not "choices" in self.srcLines[idx]: return None
        else: return self.srcLines[idx]['choices']
    def setChoices(self, idx, cIdx, text):
        if not "choices" in self.srcLines[idx]: return None
        else:
            if (cIdx):
                self.srcLines[idx]['choices'][cIdx]['enText'] = self.filter(text, self.srcLines[idx]['choices'][cIdx])
            else:
                for entry in self.srcLines[idx]['choices']:
                    entry['enText'] = self.filter(text, entry)

    def getBlockIdx(self, idx):
        return self.srcLines[idx]['blockIdx']

    def cleanLine(self, text):
        if text.startswith(">"): text = text[1:]
        return text

    def filter(self, text, target):
        filter = self.options[Options.FILTER]
        if filter:
            if "brak" in filter and not target['jpText'].startswith("（"):
                m = re.match(r"^\((.+)\)$", text, flags=re.DOTALL)
                if m:
                    text = m.group(1)

        return text

    def preprocess(self):
        filter = self.options[Options.FILTER]
        for line in self.subLines:
            if filter and "npre" in filter:
                m = re.match(r"(.+): (.+)", line.text, flags=re.DOTALL)
                if m:
                    line.name, line.text = m.group(1,2)
            if not line.effect and (line.text.startswith(">") or line.name == "Trainer"):
                line.effect = "choice"

    def duplicateSub(self, idx: int, line: TextLine = None):
        # duplicate text and choices
        self.setEn(idx, self.getEn(idx-1))
        choices = self.getChoices(idx-1)
        if choices and self.getChoices(idx):
            for c, choice in enumerate(choices):
                self.setChoices(idx, c, choice['enText'])

        # Add sub text to matching (next) block and return it as new pos
        if line:
            if idx < len(self.srcLines) - 1:
                idx += 1
                self.setEn(idx, line)
            else:
                print("Attempted to duplicate beyond last line of file. Subtitle file does not match?")
        return idx

    def isDuplicateBlock(self, idx: int) -> bool:
        if self.srcFile.getType() != "story": return False
        prevName = self.srcLines[idx - 1]['jpName']
        curName = self.srcLines[idx]['jpName']
        if not Options.DUPE_CHECK_ALL in self.options and curName not in self.skipNames: return False
        return curName == prevName and ratio(self.getJp(idx), self.getJp(idx-1)) > 0.6

class AssSubProcessor(BasicSubProcessor):
    def __init__(self, srcFile, subFile, opts) -> None:
        super().__init__(srcFile, opts)
        self.format = SubFormat.ASS
        with open(subFile, encoding='utf_8_sig') as f:
            self.preprocess(ass.parse(f))

    def cleanLine(self, text):
        text = re.sub(r"\{(?:\\([ib])1|(\\[ib])0)\}", r"<\1\2>", text) # transform italic/bold tags
        text = re.sub(r"\{.+?\}", "", text) # remove others
        text = text.replace("\\N", "\n")
        text = super().cleanLine(text)
        return text

    def preprocess(self, parsed):
        lastSplit = None
        for line in parsed.events:
            if re.match("skip", line.effect, re.IGNORECASE): continue
            if line.name == "Nameplate": continue
            if not re.search("MainText|Default|Button", line.style, re.IGNORECASE): continue
            
            if re.match("split", line.effect, re.IGNORECASE):
                if lastSplit and line.effect[-2:] == lastSplit:
                    self.subLines[-1].text += f"\n{self.cleanLine(line.text)}"
                    continue
                lastSplit = line.effect[-2:]
            else: lastSplit = None

            line.text = self.cleanLine(line.text)
            if not line.effect and line.style.endswith("Button") or line.name == "Choice":
                line.effect = "choice"
            self.subLines.append(TextLine(line.text, line.name, line.effect))
            
class SrtSubProcessor(BasicSubProcessor):
    def __init__(self, srcFile, subFile, opts) -> None:
        super().__init__(srcFile, opts)
        self.format = SubFormat.SRT
        with open(subFile, encoding='utf_8') as f:
            self.preprocess(srt.parse(f))

    def preprocess(self, parsed):
        for line in parsed:
            self.subLines.append(TextLine(line.content))
        super().preprocess()

class TxtSubProcessor(BasicSubProcessor):
    # Built on Holo's docs
    # Expects: No newlines in block, blocks separated by newline (or any number of blank lines).
    def __init__(self, srcFile, subFile, opts) -> None:
        super().__init__(srcFile, opts)
        self.format = SubFormat.TXT
        with open(subFile, "r", encoding="utf8") as f:
            self.preprocess(f)

    def preprocess(self, raw):
        self.subLines = [TextLine(l) for l in raw if common.isEnglish(l) and not re.match(r"\n+\s*", l)]

def process(srcFile, subFile, opts):
    format = subFile[-3:]
    if format == "srt":
        p = SrtSubProcessor(srcFile, subFile, opts)
    elif format == "ass":
        p = AssSubProcessor(srcFile, subFile, opts)
    elif format == "txt":
        p = TxtSubProcessor(srcFile, subFile, opts)
    else:
        print("Unsupported subtitle format.")
        raise NotImplementedError

    storyType = p.srcFile.getType()
    idx = 0
    srcLen = len(p.srcLines)
    lastChoice = [0, 0]

    for subLine in p.subLines:
        if idx == srcLen:
            print(f"File filled at idx {idx}. Next file part starts at: {subLine.text}")
            break

        # skip title logo on events and dummy text
        if p.getJp(idx).startswith("イベントタイトルロゴ表示") or re.match("※*ダミーテキスト", p.getJp(idx)):
            idx += 1
        # races can have "choices" but their format is different because there is always only 1 and can be treated as normal text
        if storyType == "story":
            if subLine.isChoice():
                if not p.getChoices(idx-1):
                    print(f"Found assumed choice subtitle, but no matching choice found at block {p.getBlockIdx(idx-1)}, skipping...")
                    continue
                if lastChoice[0] == idx: # Try adding multiple choice translations
                    try: p.setChoices(idx-1, lastChoice[1], subLine.text)
                    except IndexError: print(f"Choice idx error at {p.getBlockIdx(idx-1)}")
                else: # Copy text to all choices
                    p.setChoices(idx-1, None, subLine.text)
                lastChoice[0] = idx
                lastChoice[1] += 1
                continue # don't increment idx
            elif idx > 0 and p.getChoices(idx-1) and idx - lastChoice[0] > 0:
                print(f"Missing choice subtitle at block {p.getBlockIdx(idx-1)}")
            lastChoice[1] = 0
        
        # Add text
        if p.isDuplicateBlock(idx):
            print(f"Found gender dupe at block {p.getBlockIdx(idx)}, duplicating.")
            idx = p.duplicateSub(idx, subLine) + 1
            continue
        else:
            if len(subLine.text) == 0:
                print(f"Untranslated line at {p.getBlockIdx(idx)}")
            else:
                p.setEn(idx, subLine)
        idx += 1
    # check niche case of duplicate last line (idx is already increased)
    if idx < srcLen:
        if p.isDuplicateBlock(idx):
            print("Last line is duplicate! (check correctness)")
            p.duplicateSub(idx)
        else:
            print(f"Lacking {srcLen - idx} subtitle(s).")

    p.saveSrc()

def main():
    args = common.Args().parse()
    if args.getArg("-h"):
        common.usage("-src <translation file> -sub <subtitle file> [-filter <npre, brak, ...>] [-OVRNAMES] [-DUPEALL]",
                    "Imports translations from subtitle files. A few conventions are used.", "\n",
                    "OVRNAMES: Replace existing names with names from subs",
                    "DUPEALL: Check all lines for duplicates (default only trainer's/narration)",
                    "Filters:",
                    "npre: remove char name prefixes and extracts them to the name field",
                    "brak: remove brackets from text entirely encased in them if original is not", "\n",
                    "Conventions:",
                    "1 subtitle per game text screen. Include empty lines if needed (say, if you leave a line untranslated)",
                    "The effect field in ASS can be set to 'Split' for ALL lines that break the above. When 2 consecutive screens are both split, use 'SplitXX', where XX is a unique ID for each screen. Others formats will fail to import correctly.",
                    "For any additional lines not present in game (such as ASS effects) set the effect field to 'skip'")

    TARGET_FILE = args.getArg("-src", None)
    SUBTITLE_FILE = args.getArg("-sub", None)

    process(TARGET_FILE, SUBTITLE_FILE, {
        Options.OVERRIDE_NAMES: args.getArg("-OVRNAMES", False),
        Options.DUPE_CHECK_ALL: args.getArg("-DUPEALL", False),
        Options.FILTER: args.getArg("-filter", False) # x,y,...,
        })
    print("Successfully transferred.")

if __name__ == '__main__':
    main()