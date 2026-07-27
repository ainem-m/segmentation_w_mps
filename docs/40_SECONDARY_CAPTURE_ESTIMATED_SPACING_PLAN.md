# DICOM幾何情報欠落データ向け「推定spacing＋手動調整」実装計画

## 1. 目的と対象範囲

Secondary Captureなど、画素データはdecodeできるものの、`Pixel Spacing`、
`Image Position (Patient)`、`Image Orientation (Patient)`などの幾何情報が
欠落しているDICOMを対象とする。

利用可能なタグ、同一検査内の系列、三方向画像、画像内容からX/Y/Z spacingの
編集可能な初期候補を毎回生成し、ユーザーが三方向MPRを確認しながらspacing、
軸、回転、スライス順、cropを調整できる救済経路を追加する。

本機能は研究・教育・検証用の非臨床プレビューを目的とする。本アプリは医療機器
ではなく、生成結果は診断、治療方針の決定、治療計画、その他の医療上の判断には
使用できない。医療機器認証、診断精度保証、規制対応は対象外とする。

ただし、形状が大きく歪んだ3Dモデルを誤って生成しないため、次を必須とする。

- 推定値と確定値を分離する
- 推定根拠、confidence、制約、曖昧さを表示する
- fallback初期値を正確な推定値として表示しない
- ユーザーの明示的な確定前にAI推論を開始しない
- 確定後もNIfTIのshape、spacing、affineをreadbackしてから推論する
- 既存のOriginal CT、NIfTI、TotalSegmentator、ToothSeg経路を維持する

## 2. 現行コードから確認した事実

### 2.1 C++ DICOM normalizer

対象:

- `native/dicom_normalizer/src/main.cpp`
- `native/dicom_normalizer/src/gdcm_import.h`
- `native/dicom_normalizer/src/gdcm_import.cpp`
- `native/dicom_normalizer/tests/test_normalizer.py`

確認事項:

- GDCMでDICOM metadataと画素データを読み、画素decode成否まで監査している。
- `PixelSpacing`、`ImagePositionPatient`、`ImageOrientationPatient`、
  `SliceThickness`を読み取っている。
- `SliceThickness`は`DicomMeta`へ保持されるが、現行C++ audit JSONには
  出力されない。
- `SpacingBetweenSlices`とprivate/vendor geometry tagは未収集である。
- Secondary Captureは、実効frame数が32以上、shape一定、AXIALと明示され、
  CORONAL／SAGITTALではない場合だけ
  `secondary_capture_rescue_candidate`になる。
- CORONAL／SAGITTAL Secondary Captureは現在rejectされるため、三方向推定の
  根拠として使用できない。
- 通常CTは全fileにPixel Spacing、IPP、IOPがあり、file数、shape、spacing、
  orientationの整合条件を満たす場合に`original_ct_geometry_ok`となる。
- 完全な幾何情報を持つmixed MPR系列には、別系統の
  `viewer_export_mpr_mixed_candidate`がある。これは今回追加する幾何欠落
  Secondary Capture救済とは異なる経路である。
- `prepare-rescue`は`--patched-spacing X,Y,Z`を必須とする。
- 現行`prepare-rescue`は、選択系列を隔離し、dcm2niixでraw NIfTIを作り、
  NIfTI-1 headerの`pixdim`、qform、sformをidentity affine相当に書き換える。
- 軸入れ替え、voxel回転、slice反転、cropは行わない。
- IPPがないSecondary Capture系列では、現在の`isolate_series`に
  Instance Numberによる安定した順序保証がない。
- 現行`rescue_metadata.json`は、選択系列、警告、`patched_spacing`、
  出力パス、dcm2niix return codeを保存する。
- estimated値、confirmed値、根拠、confidence、transform、registration error、
  元データ内容hashは保存しない。
- 現行のFNV hashは入力pathのhashであり、元データ内容のhashではない。
- audit JSONに出るdecoded checksumも最初のfileだけであり、入力全体の
  再現性hashには使用できない。

### 2.2 Swift UI

対象:

- `native/macos/TotalSegmentatorWrapperForMac/AppState.swift`
- `native/macos/TotalSegmentatorWrapperForMac/Views.swift`
- `native/macos/TotalSegmentatorWrapperForMac/CommandBuilder.swift`
- `native/macos/TotalSegmentatorWrapperForMac/ProcessSupport.swift`

確認事項:

- DICOM folderを選ぶと、Python CLI経由でC++ normalizerのauditを実行する。
- `original_ct_geometry_ok`があれば、現在は最初のclean候補を選択し、
  `convert-clean`を自動開始する。
- clean変換に成功すると、生成NIfTIを通常NIfTI入力として設定し、入力・作成内容
  画面へ戻る。
- Swiftが現在解釈する救済候補は
  `viewer_export_mpr_mixed_candidate`だけである。
- `secondary_capture_rescue_candidate`用のSwift model、画面、
  CommandBuilder呼び出しは存在しない。
- clean候補もviewer-export候補もなければ、Secondary Capture救済候補が
  audit JSONにあっても汎用的な`dicom_audit_failed`へ進む。
- 既存viewer-export救済は、変換後に三方向PGMを表示し、ユーザーが確認するまで
  NIfTI入力として採用しない。
- DICOM監査、clean変換、viewer-export変換の処理中は
  `CommandBuilder.runCommand`を呼ばず、AI推論を開始しない。
- viewer-export確認後も通常の入力画面へ戻り、ユーザーが作成ボタンを押して
  初めて推論を開始する。
- 結果画面からのToothSeg追加推論は既存primary推論と分離されている。

### 2.3 Python、dcm2niix、推論

対象:

- `src/totalsegmentator_wrapper_mac/dicom_normalizer_bridge.py`
- `src/totalsegmentator_wrapper_mac/cli.py`
- `src/totalsegmentator_wrapper_mac/runner_totalseg.py`
- `src/totalsegmentator_wrapper_mac/toothseg_postprocess.py`

確認事項:

- Python bridgeとCLIに`dicom-normalizer-prepare-rescue`があり、
  `patched_spacing`をC++へ渡せる。
- 現行Python bridgeは推定を行わず、C++ subprocessの起動と結果受け渡しを
  担当する。
- Python packageにはNumPy、nibabel、scikit-imageがある。
- 専用のregistration moduleやSimpleITK依存はない。
- dcm2niixはclean CTと現行rescueのDICOM-to-NIfTI変換に使われる。
- TotalSegmentator runnerはNIfTI-firstであり、DICOM folderを直接推論へ
  渡さない。
- ToothSegは既存結果から明示的に追加実行できる。

### 2.4 現行metadata schema

現行schema:

- `totalsegmentator_wrapper_mac.dicom_normalizer.audit.v1`
- `totalsegmentator_wrapper_mac.dicom_normalizer.audit_failure.v1`
- `totalsegmentator_wrapper_mac.dicom_normalizer.convert_clean.v1`
- `totalsegmentator_wrapper_mac.dicom_normalizer.rescue.v1`
- `totalsegmentator_wrapper_mac.dicom_normalizer.rescue_validation.v1`
- `totalsegmentator_wrapper_mac.dicom_normalizer.viewer_export.v1`
- `totalsegmentator_wrapper_mac.input_provenance.v1`

不足:

- estimated spacingとconfirmed spacingの分離
- spacing source
- confidenceと根拠
- 使用系列と役割
- 使用タグと値の整合性
- registration errorと複数解
- axis permutation
- rotation
- slice reversal
- crop
- calibration
- 入力内容hash
- estimator versionとdeterministic seed

## 3. 設計原則

### 3.1 通常経路と救済経路を分離する

- `original_ct_geometry_ok`が1件以上ある場合は、既存clean経路を優先する。
- 幾何欠落救済候補は、clean候補がない場合だけ使用する。
- 既存`viewer_export_mpr_mixed_candidate`は今回の救済と混同しない。
- CORONAL／SAGITTAL Secondary Captureは推定根拠として保持するが、
  primary volumeへ自動昇格させない。
- 通常CTのclassification条件は緩めない。

### 3.2 候補生成と精度主張を分離する

- 情報が不足していても、有限・正値の編集可能な初期候補は毎回返す。
- 候補が生成できたことを「正確に推定できた」とは扱わない。
- fallback値は`fallback_initial_candidate`として保存・表示する。
- confidenceはoverallだけでなく軸ごとに保持する。
- 複数解が同程度の場合はtop-K候補を残し、一意解として表示しない。

### 3.3 previewと最終NIfTIで同じtransformを使う

次の順序をcanonical transformとして固定する。

1. source stack ordering
2. axis permutation
3. 90度rotation
4. slice reversal
5. crop
6. spacingとlocal affineの適用

Swift previewとC++の最終NIfTI writerが同じ
`RescueGeometryTransform`を解釈する。

### 3.4 推論の明示的な確認ゲート

- auditでは推論しない。
- estimationでは推論しない。
- MPR、距離計測、疑似3Dでは推論しない。
- 最終ボタン「この寸法で3Dプレビューを作成」をconfirmation eventとする。
- `prepare-rescue`とNIfTI readbackが成功するまでTotalSegmentatorを起動しない。
- v1の救済primary推論はTotalSegmentatorに限定する。
- ToothSegは既存結果画面からの明示操作だけで開始する。

## 4. 推奨アーキテクチャ

```text
DICOM audit
  |
  +-- clean CT候補あり
  |     `-- 既存 convert-clean -> NIfTI入力画面
  |
  +-- cleanなし、幾何欠落救済候補あり
  |     `-- 救済セッション
  |           -> preview stack生成
  |           -> spacing推定
  |           -> Swift救済調整画面
  |           -> user confirmation
  |           -> prepare-rescue
  |           -> shape/spacing/affine readback
  |           -> TotalSegmentator
  |           -> 結果画面
  |           -> 必要ならToothSeg
  |
  +-- cleanなし、既存viewer-export候補あり
  |     `-- 既存viewer-export救済
  |
  `-- 候補なし
        `-- 安全な監査失敗画面
```

### 4.1 C++／GDCM

担当:

- DICOM解析
- 標準タグ取得
- allowlist済みprivate/vendor tag取得
- 画素decode
- multi-frame展開
- 安定したinstance／frame順
- 同一Study内の系列関連付け
- input content hash
- raw preview stack生成
- confirmed transformの検証
- 決定論的なpseudo-NIfTI生成
- shape、spacing、affine readback

幾何欠落Secondary Captureのfinal writerはC++で実装することを推奨する。
dcm2niixによるSCの非空間orderingやbogus affine推定へ依存しないためである。

### 4.2 Python

担当:

- C++が生成した数値配列のforeground検出
- 黒背景、余白、枠、文字候補mask
- 三方向位置合わせ
- spacing候補生成
- top-K解
- confidence、根拠、registration error
- BO／STなど別再構成系列のcross-validation

制約:

- Pythonは元DICOMを直接開かない。
- 元path、UID、Series Description、患者タグをPythonへ渡さない。
- pixel由来arrayはmasking確認までは機微データとして扱う。
- hash名のローカル専用cacheに保存する。
- pixel由来情報をerror JSONやlogへ出さない。
- maskingと保存境界を十分に検証できなければ、Python registration handoffを
  停止し、native処理またはmanual-onlyへ限定する。

### 4.3 Swift

担当:

- 救済workflow state
- 系列一覧
- 自動推定進捗とcancel
- X/Y/Z編集
- stepper
- X/Y同値lock
- axis permutation
- 90度rotation
- slice reversal
- crop
- MPR描画
- 距離計測と既知長calibration
- inferenceを使わない疑似3D
- user confirmation
- readback後の既存推論経路接続

### 4.4 dcm2niix

- 既存`original_ct_geometry_ok -> convert-clean`を継続する。
- 完全な標準幾何を持つ通常CTの変換を担当する。
- 既存viewer-export救済は現行仕様を維持する。
- 幾何欠落Secondary Captureのfinal orderingとaffineは任せない。

## 5. 推定アルゴリズム

### 5.1 Stage 0: 軸と単位の契約

DICOM `PixelSpacing`は次の順である。

```text
[row spacing, column spacing]
```

未変換stackからNIfTIへ対応させる初期規則:

```text
X = column spacing
Y = row spacing
Z = slice center-to-center spacing
```

axis permutation後は、同じ変換をspacing、voxel array、crop、calibration、
affineへ適用する。

患者座標は不明なため、`coordinate_frame=rescue_local`と明示する。
qform/sform codeは、nibabel、TotalSegmentator、readbackによる事前spikeで
決定する。患者orientationを架空に生成しない。

### 5.2 Stage 1: 標準タグ

優先順位:

1. Pixel Spacing＋IPP＋IOP
2. Enhanced DICOM functional groups
3. Spacing Between Slices
4. Slice Thickness

Z spacingは可能な場合、IPPをIOPのslice normalへ投影した隣接差のmedianを
採用する。

`SliceThickness`は公称slice厚であり、slice中心間距離とは限らないため、
Spacing Between SlicesやIPPよりconfidenceを下げる。

### 5.3 Stage 2: private/vendor tag

- メーカー、private creator、tag、VR、単位、意味をfixtureで検証する。
- allowlist済みの数値tagだけを使用する。
- 未知private tagの値をmetadataやlogへ出さない。
- allowlist mappingにはversionを付ける。
- 標準タグと矛盾する場合、標準タグを優先し、矛盾をconfidence根拠へ残す。

### 5.4 Stage 3: 系列関連付け

内部的には次で関連付ける。

- Study Instance UID
- Frame of Reference UID
- Series Number
- Series Description
- Acquisition Number
- Rows／Columns
- Slice Thickness
- Instance Number範囲
- Manufacturer／model

外部metadataではUIDとdescriptionをhashまたは安全なroleへ置き換える。

系列role:

- `primary`: pseudo-volume生成元
- `reference_coronal`
- `reference_sagittal`
- `cross_validation`
- `excluded`

BOとSTは独立した再構成groupとして扱い、voxelを融合しない。

### 5.5 Stage 4: slice順とplane仮説

slice順の優先順位:

1. IPPをIOP normalへ投影
2. In-Stack Position Number
3. Instance Number
4. Acquisition順序
5. filename
6. 隣接画像の連続性

filenameと画像連続性だけの場合はlow confidenceとする。正順と逆順を双方評価する。

plane仮説:

- AXIAL／CORONAL／SAGITTAL
- XYZの6 permutation
- 0／90／180／270度
- slice順の正転／反転

descriptionやImageTypeはseedとして使用するが、唯一の確定根拠にはしない。

### 5.6 Stage 5: 余白、文字、枠、crop

- 外周から連結する黒背景を検出する。
- 多数sliceに固定された高輝度領域、高edge密度領域をoverlay候補にする。
- UI枠や文字候補をregistration metricから除外する。
- sliceごとのcropではなく、系列全体のconsensus cropを使用する。
- anatomyを切る恐れがある場合はsuggested cropとして表示し、自動確定しない。
- crop前後のvoxel範囲を保存する。
- RGB、palette、8-bit screen captureには別quality flagを立てる。

### 5.7 Stage 6: 系列枚数とFOVによる初期seed

単純な枚数計算は初期seedとしてだけ使用する。

例:

- AXIAL Z候補: AXIAL Spacing Between Slices、なければSlice Thickness
- X extent候補: SAGITTAL枚数×SAGITTAL slice step
- Y extent候補: CORONAL枚数×CORONAL slice step
- X spacing候補: X extent÷AXIAL有効foreground幅
- Y spacing候補: Y extent÷AXIAL有効foreground高さ

余白、crop、screen zoomがあるため、この段階のconfidenceは最大でも
low～mediumとする。

### 5.8 Stage 7: 三方向位置合わせ

primary AXIAL stackからCORONAL／SAGITTAL resliceを生成し、実系列と比較する。

探索変数:

- spacing X/Y/Z
- plane
- axis permutation
- 90度rotation
- slice reversal
- crop
- in-plane offset
- reference系列内のslice位置
- 必要な場合だけ限定的なscreen zoom scale

探索:

1. 1/8～1/4 downsampleで離散仮説を探索
2. 上位候補を1/2解像度で最適化
3. 代表sliceだけを原解像度で評価
4. 複数初期値と固定seedで再実行
5. top-K解を保存

metric:

- masked mutual information
- edge alignment
- foreground overlap
- slice continuity

非一意判定:

- 1位と2位のobjective差が小さい
- 異なるaxis仮説が同程度
- パラメータが探索境界へ張り付く
- restartごとに解が変わる
- BO／ST間のspacing差が大きい
- registration residualが閾値以上
- foreground overlapが不足

この場合、候補値は表示するが`confidence=low`または`ambiguous`とする。

### 5.9 Stage 8: BO／ST cross-validation

- BO、STを独立に推定する。
- 同一解剖範囲と判断できた場合だけspacing、FOV、axisを比較する。
- 差が閾値以下ならconfidence根拠に追加する。
- 差が大きい場合は両候補を表示し、confidenceを下げる。
- 一方の系列を他方へ自動置換しない。
- 三方向系列を高解像度CTとして融合しない。

### 5.10 Stage 9: 既知長calibration

- 軸とほぼ平行な線は対応する1軸だけを更新する。
- X/Y同値lock中は、斜め線から共通in-plane scaleを解ける。
- XYZ独立状態の任意方向1本では解が不足するため、追加計測を求める。
- 複数線ではleast-squaresでspacingを解き、residualを表示する。
- 計測は現在のaxis、rotation、crop、spacingを適用した座標で行う。
- 計測点、plane、既知長、更新軸、residualをmetadataへ保存する。

### 5.11 最終fallback

情報不足でも次を返す。

```text
status: fallback_initial_candidate
confidence: unknown
spacing: 有限・正値の編集用初期値
```

Slice Thicknessだけがある場合:

- ZはSlice Thickness候補
- X/Yは別根拠がなければfallback

すべて不明の場合:

- 編集用のunit spacing候補などを返す
- UIに「仮の初期値です。寸法は確認できていません」と表示する

## 6. confidenceモデル

overallとper-axisを保持する。

```text
high
medium
low
unknown
```

基準:

- `high`: 一貫した標準幾何タグ、またはfixtureで検証済みvendor tag
- `medium`: 複数の独立根拠が整合し、registrationも安定
- `low`: Slice Thickness、系列枚数、画像位置合わせなど弱い根拠
- `unknown`: fallback、非収束、複数解

保存項目:

- label
- numeric score
- per-axis label
- positive evidence
- limitations
- convergence
- top-2 margin
- cross-series disagreement

UI例:

- 「標準DICOMタグから取得」
- 「三方向画像から推定・信頼度: 中」
- 「仮の初期値です。寸法は確認できていません」
- 「候補が複数あります」

## 7. metadata schema案

canonical artifact:

```text
rescue_geometry.v2.json
```

例:

```json
{
  "schema": "totalsegmentator_wrapper_mac.rescue_geometry.v2",
  "workflow_status": "confirmed",
  "source": {
    "content_manifest_sha256": "...",
    "ordered_instance_manifest_sha256": "...",
    "hash_algorithm": "sha256",
    "series_count": 6,
    "file_count": 542
  },
  "axis_convention": {
    "dicom_pixel_spacing_order": ["row", "column"],
    "estimated_spacing_order": ["x", "y", "z"],
    "initial_mapping": {
      "x": "column",
      "y": "row",
      "z": "slice"
    },
    "coordinate_frame": "rescue_local"
  },
  "estimate": {
    "estimated_spacing_xyz": [0.58, 0.61, 0.9375],
    "spacing_source": [
      "slice_thickness",
      "tri_planar_registration",
      "cross_reconstruction_validation"
    ],
    "status": "estimated",
    "confidence": {
      "overall": "medium",
      "per_axis": {
        "x": "low",
        "y": "low",
        "z": "medium"
      },
      "score": 0.63,
      "reasons": [
        "slice_thickness_consistent",
        "tri_planar_registration_converged"
      ],
      "limitations": [
        "screen_capture_crop_unknown"
      ]
    },
    "alternatives": [
      {
        "spacing_xyz": [0.64, 0.64, 0.9375],
        "score": 0.61
      }
    ]
  },
  "evidence": {
    "used_series": [
      {
        "series_hash": "...",
        "role": "primary",
        "plane": "axial",
        "reconstruction_group": "BO",
        "file_count": 138
      }
    ],
    "used_dicom_tags": [
      {
        "tag": "0018,0050",
        "name": "SliceThickness",
        "value_mm": 0.9375,
        "consistency": "all_equal"
      }
    ],
    "registration": {
      "metric": "masked_mutual_information",
      "converged": true,
      "residual": 0.18,
      "top2_score_margin": 0.02,
      "cross_series_disagreement_mm": [0.06, 0.04, 0.0]
    }
  },
  "confirmed": {
    "confirmed_spacing_xyz": [0.60, 0.60, 0.9375],
    "manual_changed": true,
    "changed_axes": ["x", "y"],
    "confirmed_at": "ISO-8601"
  },
  "transform": {
    "axis_permutation": ["x", "y", "z"],
    "rotation_quarter_turns": 0,
    "slice_order_reversed": false,
    "crop_voxels_xyz": {
      "min": [32, 24, 0],
      "max_exclusive": [480, 488, 138]
    }
  },
  "calibrations": [],
  "output_validation": {
    "shape": [448, 464, 138],
    "spacing_xyz": [0.60, 0.60, 0.9375],
    "affine_consistent": true,
    "input_hash_matches": true
  },
  "algorithm": {
    "normalizer_version": "...",
    "estimator_version": "...",
    "configuration_version": "...",
    "random_seed": 0
  },
  "warnings": [
    "secondary_capture",
    "geometry_inferred",
    "burned_in_annotation",
    "non_diagnostic_preview"
  ]
}
```

保存原則:

- 元DICOMのUID、患者タグ、元path、元filenameを保存しない。
- Series Descriptionを外部metadataへ保存しない。
- private tagはallowlist済み数値だけを保存する。
- input hashは全instanceを安定順でSHA-256し、manifest全体もSHA-256する。
- estimator version、設定version、seedを保存する。
- manual変更はspacing、axis、rotation、reverse、crop、calibrationごとに保持する。
- 同一input hashと同一confirmed geometryから同じNIfTIを再生成できる。
- error JSONはcode、stage、safe reason、hash prefix、tool versionに限定する。

## 8. Swift UI

新規画面:

```text
DicomRescueView
```

### 8.1 注意表示

```text
寸法情報を画像から推定しています。生成結果は参考用です。
```

重い同意画面は追加しない。

### 8.2 系列一覧

表示:

- BO／STなどの再構成group
- AXIAL／CORONAL／SAGITTAL
- 方向不明
- file／frame数
- Rows×Columns
- Slice Thickness
- spacing根拠
- primary／reference／cross-validation／excluded

### 8.3 推定進捗

状態:

- metadata確認中
- 画像準備中
- 余白・文字候補検出中
- 三方向位置合わせ中
- BO／ST整合確認中
- 候補作成済み
- 低信頼
- 複数解
- 手動のみ
- cancel

### 8.4 手動調整

- X/Y/Z TextField
- X/Y/Z Stepper
- X/Y同値lock
- axis permutation
- 90度rotation
- slice reversal
- crop handles
- 自動推定値へ戻す
- alternative candidate選択
- 極端値のinline警告

### 8.5 MPR

- AXIAL／CORONAL／SAGITTAL
- crosshair連動
- spacingに応じたaspect ratio
- axis、rotation、reverse、cropの即時反映
- zoom／pan
- 距離計測
- 既知長入力

subprocessを操作のたびに起動しない。C++が作ったdownsample preview volumeを
Swiftがmemory-mapし、Accelerate/vImageまたはMetalで描画する。

目標:

```text
spacing、rotation、flip操作から100 ms以内にpreview更新
```

### 8.6 疑似3D

AI inferenceを使わないdownsampled MIP、volume ray preview、またはslice-boxを
使用する。

目的:

- 全体aspect ratioの確認
- 極端な伸長／圧縮の確認
- crop範囲の確認

segmentation結果の代替として表示しない。

### 8.7 最終操作

ボタン:

```text
この寸法で3Dプレビューを作成
```

処理:

1. current geometryをconfirmedとして保存
2. input content hashを再確認
3. `prepare-rescue`
4. pseudo-NIfTI生成
5. shape／spacing／affine readback
6. 一致時だけTotalSegmentator開始

## 9. UI状態遷移

```text
idle
  -> auditing
  -> rescueAvailable
  -> exportingPreviewStack
  -> estimating
  -> editableReady
  -> userModified
  -> confirmed
  -> preparingNifti
  -> validatingNifti
  -> inferenceRunning
  -> result
```

失敗状態:

```text
estimateFailed -> manualOnly
registrationAmbiguous -> editableReady
previewFailed -> manualOnly
prepareFailed -> editableReady
readbackMismatch -> editableReady
inputHashChanged -> auditing
```

どの失敗でもクラッシュせず、可能な限りmanual inputへ戻る。

## 10. 実行フロー

### 10.1 通常DICOM

```text
audit
-> original_ct_geometry_ok
-> 既存convert-clean
-> 既存NIfTI入力
-> ユーザー作成操作
-> 既存推論
```

### 10.2 幾何欠落救済

```text
audit
-> clean候補なし
-> rescue候補あり
-> 救済画面
-> spacing自動推定
-> 三方向MPR
-> ユーザー調整
-> ユーザー確定
-> prepare-rescue
-> pseudo-NIfTI
-> shape/spacing/affine readback
-> TotalSegmentator
-> 結果画面
-> 必要ならToothSeg追加推論
```

### 10.3 推論開始禁止点

次では推論しない。

- audit
- series grouping
- tag評価
- preview stack生成
- spacing推定
- registration
- MPR
- calibration
- 疑似3D
- 未確定状態
- readback不一致状態

## 11. 保存と再現性

推奨ディレクトリ:

```text
runs/dicom_rescue_<session-id>/
  audit/
    audit_snapshot.json
  estimate/
    source_manifest.json
    rescue_estimate.json
  cache/
    <hash>.preview.raw
    <hash>.mask.raw
  confirmed/
    rescue_geometry.v2.json
  output/
    rescue_volume.nii
    rescue_validation.json
  preview/
    ...
  logs/
    estimator.log
    prepare_rescue.log
```

規則:

- 元DICOMはread-onlyとする。
- 元DICOMの常設copyを作らない。
- cacheと成果物は元入力と別ディレクトリに保存する。
- retryにはattempt番号を付ける。
- 既存case、primary log、結果を削除・上書きしない。
- inference caseは一意な別ディレクトリへ作る。
- latest情報が必要なら既存成果物を消さず、pointer JSONだけatomic更新する。
- 同一input hashと同一confirmed geometryからbyte-identicalなvoxel payload、
  header、affineを再生成できるようにする。

## 12. 失敗・低信頼時の扱い

### 三方向系列がない

- 使用可能な標準タグと単一stackから候補を出す。
- confidenceをlow／unknownとする。
- 手動入力へ進める。

### Slice Thicknessもない

- Spacing Between Slices、private tag、cross-series、registrationを試す。
- 根拠がなければfallback初期値を出す。
- 確定値として表示しない。

### 系列間で範囲が一致しない

- 各系列を独立表示する。
- primary／reference選択を求める。
- 自動融合しない。

### registrationが収束しない

- `converged=false`を保存する。
- 初期候補＋手動入力へ移行する。

### 複数解が同程度

- top候補を表示する。
- `ambiguous`と表示する。
- 一意解として自動確定しない。

### 大きな文字や枠

- maskしてmetricから除外する。
- 除外できなければ自動registrationを停止する。

### 軸方向を判定できない

- 離散仮説を候補として表示する。
- ユーザーがaxis、rotation、reverseを選べるようにする。

### 手動値が極端

- NaN、Inf、0以下はblockする。
- 異常に小さい／大きいFOV、voxel数、memory量はinline警告する。
- 技術上生成不能な値はblockする。
- 警告範囲は設定versionとともに保存する。

### NIfTI readback不一致

- AI推論を開始しない。
- errorをsafe JSONへ保存する。
- 救済編集画面へ戻す。

## 13. 変更対象ファイル

### 13.1 既存ファイル

- `native/dicom_normalizer/src/main.cpp`
- `native/dicom_normalizer/src/gdcm_import.h`
- `native/dicom_normalizer/src/gdcm_import.cpp`
- `native/dicom_normalizer/CMakeLists.txt`
- `native/dicom_normalizer/tests/test_normalizer.py`
- `src/totalsegmentator_wrapper_mac/dicom_normalizer_bridge.py`
- `src/totalsegmentator_wrapper_mac/cli.py`
- `native/macos/TotalSegmentatorWrapperForMac/AppState.swift`
- `native/macos/TotalSegmentatorWrapperForMac/Views.swift`
- `native/macos/TotalSegmentatorWrapperForMac/CommandBuilder.swift`
- `scripts/build_mac_app.sh`
- `tests/test_dicom_normalizer_bridge.py`
- `tests/test_dicom_audit.py`
- `tests/test_swiftui_navigation_coverage.py`
- `tests/test_mac_app_packaging.py`
- non-clinical wording関連test
- DICOM境界・救済仕様文書

### 13.2 追加推奨ファイル

- `native/dicom_normalizer/src/rescue_manifest.h`
- `native/dicom_normalizer/src/rescue_manifest.cpp`
- `native/dicom_normalizer/src/rescue_stack.h`
- `native/dicom_normalizer/src/rescue_stack.cpp`
- `native/dicom_normalizer/src/nifti_writer.h`
- `native/dicom_normalizer/src/nifti_writer.cpp`
- `src/totalsegmentator_wrapper_mac/rescue_geometry.py`
- `src/totalsegmentator_wrapper_mac/rescue_registration.py`
- `src/totalsegmentator_wrapper_mac/rescue_estimator_cli.py`
- `native/macos/TotalSegmentatorWrapperForMac/DicomRescueModels.swift`
- `native/macos/TotalSegmentatorWrapperForMac/DicomRescueView.swift`
- `native/macos/TotalSegmentatorWrapperForMac/RescueMPRRenderer.swift`
- `tests/test_rescue_geometry.py`
- `tests/test_rescue_metadata_safety.py`
- synthetic fixture generator
- golden metadata fixtures

`main.cpp`は既に大きいため、新処理をすべて追加せず、manifest、stack、
NIfTI writerを分離する。

## 14. テスト計画

### 14.1 C++ unit／synthetic

- 標準幾何タグあり
- Pixel Spacingのみ欠落
- Z spacingのみ欠落
- Slice Thicknessのみ
- Spacing Between Slicesあり
- allowlist済みprivate tag
- 未知private tagを無視
- 三方向Secondary Capture
- 単一方向Secondary Capture
- multi-frame Secondary Capture
- Instance Number順
- 欠番
- Instance Number重複
- slice逆順
- axis permutation
- 90度rotation
- crop
- RGB／MONOCHROME
- content hashの決定性
- 元DICOM非変更
- NIfTI shape、pixdim、affine readback
- 同一入力＋同一confirmationでbyte-identical出力

### 14.2 Python estimator

- 余白あり
- 文字あり
- 枠あり
- screen zoomあり
- cropあり
- 三方向の範囲不一致
- BO／ST整合
- BO／ST不整合
- registration収束
- registration非収束
- top-2同率
- 軸不明
- 単一方向fallback
- 既知長の軸方向calibration
- diagonal一本でXYZ独立解を作らない
- extreme値
- NaN／Inf
- deterministic seed
- error payloadにPHI、path、UID、descriptionがない

### 14.3 Swift

- audit優先順位: clean > SC rescue > viewer export > failure
- SC候補で`dicom_audit_failed`へ落ちない
- estimating
- cancel
- manualOnly
- X/Y/Z編集
- X/Y同値lock
- stepper
- reset
- axis
- rotation
- slice reversal
- crop
- MPR即時更新
- calibration
- confirmation前にrun commandを生成しない
- readback失敗時に推論しない
- confirmation後だけTotalSegmentator
- 結果画面のToothSeg追加経路
- NIfTI直接入力回帰
- 通常DICOM回帰
- viewer-export回帰

### 14.4 統合

- C++ audit -> estimator -> Swift schema decode
- confirmed JSON -> prepare-rescue
- shape／spacing／affine readback
- TotalSegmentatorまでのmock integration
- primary pathでunexpected fallbackを0件にする
- fallback許容testではreasonとdegraded stateを検証する
- packaged C++／Python／Swift schema version一致
- bundled runtime command smoke

### 14.5 実データ

Case03相当のBO／ST実データで確認する。

- 全系列のplane識別
- slice順
- 文字／枠mask
- 三方向MPR
- spacing変更時のaspect ratio
- crop
- BO／ST推定差
- top候補の安定性
- NIfTI readback
- TotalSegmentator結果が枠・文字だけを追っていないこと

実データは現在リポジトリに存在しないため、推定精度と所要時間は未検証である。

## 15. 実装順序

### Phase 1: 座標とschema契約

- row／columnからXYZへのmapping
- transform順序
- confidence
- safe error
- content hash
- schema version
- qform／sform spike

### Phase 2: C++ audit v2

- Slice Thickness出力
- Spacing Between Slices
- safe private allowlist
- Study内関連
- SC reference分類
- 通常CT分類回帰

### Phase 3: 決定論的stack生成

- instance／frame順
- content hash
- raw stack
- downsample preview
- source manifest

### Phase 4: manual-only縦切り

- Swift救済画面
- XYZ入力
- confirmation
- pseudo-NIfTI
- readback

自動推定なしでも使用可能な救済経路を先に完成させる。

### Phase 5: transformとinteractive MPR

- axis
- rotation
- reverse
- crop
- previewとfinal writerの共通transform

### Phase 6: 安価な推定

- 標準tag
- private tag
- Slice Thickness
- 系列枚数
- foreground extent
- fallback

### Phase 7: 既知長calibration

- axis-aware計測
- X/Y lock
- 複数計測
- residual

### Phase 8: 三方向registration

- masked multi-scale mutual information
- top-K
- ambiguity
- BO／ST cross-validation
- cancel、timeout、memory上限

### Phase 9: 推論接続

- confirmation token
- input hash再確認
- prepare-rescue
- readback
- TotalSegmentator
- 結果画面ToothSeg

### Phase 10: packaging、実データ、回帰

- bundled dependency
- notarization
- performance
- PHI境界
- UI目視
- 既存全経路

## 16. リスクと停止条件

### spacingとscreen zoom／cropを識別できない

停止条件:

- restartごとに解が変わる
- top-2 marginが小さい
- BO／ST差が大きい
- parameterが探索境界へ張り付く

対応:

- auto-registrationをshippingしない
- manual-onlyは継続する

### axis／affineが一貫しない

停止条件:

- previewとNIfTI readbackが一致しない
- nibabelとTotalSegmentatorで向きが変わる

対応:

- qform／sform spikeとgolden affine testが通るまで推論接続しない

### Secondary Captureの画素値がHUではない

geometry推定ではHUを復元できない。windowed grayscale、RGB、overlay支配などは
別の入力品質問題である。

停止条件:

- grayscale volumeとして扱えない
- anatomyよりoverlayが支配的
- TotalSegmentator入力前提を満たさない

対応:

- HU回復やCT値精度を主張しない
- AI実行をblock、またはunsupportedとする

### burned-in PHI

停止条件:

- mask前のpixelが管理外cacheへ出る
- path、UID、descriptionがPythonやerror JSONへ出る
- masking検証ができない

対応:

- Python registration handoffを停止
- native処理またはmanual-onlyへ限定

### BO／STの範囲が異なる

- 同一volumeとして融合しない
- cross-validationだけに使う
- 不一致をconfidenceへ反映する

### dcm2niixがSC順序やaffineを独自推定する

- SC final writerをC++で決定論的に実装する
- dcm2niixはclean経路に限定する

### dependencyとpackage size

- まず既存NumPy／scikit-imageでprototypeする
- SimpleITK／ITK追加は実測後に判断する
- bundle size、notarization、起動時間が許容できなければ追加しない

### memory／処理時間

- downsample preview
- stream decode
- cancel
- timeout
- deterministicな探索上限

実データで設定した上限を超える場合、自動registrationを無効化する。

### 実データ未検証

BO／ST実データで次を確認するまでconfidence thresholdを確定しない。

- orientation
- spacing安定性
- MPR
- ambiguity
- latency
- memory
- visual QA

## 17. 受け入れ条件

- [ ] 通常DICOMは従来どおり`original_ct_geometry_ok -> convert-clean`を通る。
- [ ] NIfTI直接入力経路を変更しない。
- [ ] 既存viewer-export救済を変更・混同しない。
- [ ] 救済候補がある場合、汎用`dicom_audit_failed`だけで終了しない。
- [ ] 毎回、編集可能なX/Y/Z初期候補を表示する。
- [ ] fallback値を正確な推定値として表示しない。
- [ ] 推定方法、根拠、confidenceを表示する。
- [ ] 使用系列、使用タグ、registration errorを保存する。
- [ ] X/Y/Zを手動変更できる。
- [ ] X/Y同値lockを使用できる。
- [ ] stepperと自動推定値へのresetを使用できる。
- [ ] axis、90度rotation、slice reversal、cropを変更できる。
- [ ] 同じtransformがMPRと最終NIfTIへ適用される。
- [ ] 変更が三方向MPRへ即時反映される。
- [ ] 距離計測と既知長calibrationを使用できる。
- [ ] inferenceなしの疑似3Dを確認できる。
- [ ] 明示的な確定前にAI subprocessを開始しない。
- [ ] 確定後に`prepare-rescue`でpseudo-NIfTIを生成できる。
- [ ] shape、spacing、affine readbackが一致しなければ推論を開始しない。
- [ ] readback成功後だけ既存TotalSegmentatorへ進む。
- [ ] ToothSegは結果画面からの明示操作だけで開始する。
- [ ] estimated spacingとconfirmed spacingを両方保存する。
- [ ] input content hash、algorithm version、transform、crop、calibrationを保存する。
- [ ] 元DICOMを変更しない。
- [ ] 救済成果物を一意な専用ディレクトリへ保存する。
- [ ] 既存結果とprimary logを上書きしない。
- [ ] error JSONに患者情報、UID、Series Description、元pathを含めない。
- [ ] 低信頼、非収束、複数解でもクラッシュせずmanual inputへ進める。
- [ ] Original CT、NIfTI、TotalSegmentator、ToothSegの回帰testが通る。
- [ ] 実データで三方向MPRと最終shape／spacingを目視確認する。

## 18. 計画段階の検証結果

コード変更前のbaselineとして次を確認した。

```text
PYTHONPATH=src python3 -m unittest \
  tests.test_dicom_normalizer_bridge \
  tests.test_dicom_audit \
  tests.test_swiftui_navigation_coverage

結果: 50 tests OK
```

```text
python3 native/dicom_normalizer/tests/test_normalizer.py \
  build/dicom_normalizer/totalsegmentator-wrapper-dicom-normalizer \
  /opt/homebrew/bin/gdcmconv

結果: 11 synthetic cases OK
```

初回Python test実行は`PYTHONPATH=src`未設定のため2 moduleがimport errorとなった。
正しいrepository test設定で再実行し、50件すべて成功した。

実データがないため、三方向registrationの精度、BO／ST cross-validation、
処理時間、memoryは未検証であり、実装時の停止条件として扱う。
